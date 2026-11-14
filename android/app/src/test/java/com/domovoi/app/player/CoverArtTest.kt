package com.domovoi.app.player

import com.domovoi.app.net.ApiClient
import com.domovoi.app.testing.Bytecode
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Library cover art on the phone: the URL every surface asks for (the list
 * row, the track drawer, the player, the mini player, the media session),
 * how a cover path becomes something an image loader can open, the miss
 * memo, and that the screens and the media session are wired to them.
 */
class CoverArtTest {

    private val api = ApiClient(baseUrlProvider = { "http://192.168.1.20:6369" })

    @After fun clean() {
        CoverArt.forgetMisses()
        CoverArt.appForegrounded()   // never leave a test "away"
    }

    @Test fun libraryPathIsTheWebBackendsCoverRoute() {
        assertEquals("/api/music/library/12/cover", CoverArt.libraryPath(12))
        assertEquals("/api/music/library/5232/cover", CoverArt.libraryPath(5232))
    }

    @Test fun aLibraryItemCarriesItsCoverForTheMediaSession() {
        val item = PlayItem.fromTrack(5232, "B-Boy Bouillabaisse", "Beastie Boys", null, 141.0)
        assertEquals(CoverArt.libraryPath(5232), item.coverPath)
        assertEquals("/api/music/library/5232/audio", item.src)
    }

    @Test fun serverPathsResolveAgainstTheActiveServer() {
        assertEquals(
            "http://192.168.1.20:6369/api/music/library/12/cover",
            CoverArt.model(CoverArt.libraryPath(12), api::absolute),
        )
        val emulator = ApiClient(baseUrlProvider = { "http://10.0.2.2:6390" })
        assertEquals(
            "http://10.0.2.2:6390/api/music/library/7/cover",
            CoverArt.model("/api/music/library/7/cover", emulator::absolute),
        )
    }

    @Test fun phoneArtAndFullUrlsPassThroughUntouched() {
        // A song saved on the phone carries MediaStore album art; prefixing
        // it with the server made the mini player's art a dead URL.
        val content = "content://media/external/audio/albumart/42"
        assertEquals(content, CoverArt.model(content, api::absolute))
        val podcast = "https://feeds.example/show/art.jpg"
        assertEquals(podcast, CoverArt.model(podcast, api::absolute))
    }

    @Test fun noCoverIsNoModel() {
        assertNull(CoverArt.model(null, api::absolute))
        assertNull(CoverArt.model("", api::absolute))
        assertNull(CoverArt.model("  ", api::absolute))
    }

    @Test fun onlyA404IsRememberedAsMissing() {
        val a = "http://192.168.1.20:6369/api/music/library/1/cover"
        val b = "http://192.168.1.20:6369/api/music/library/2/cover"
        val c = "http://192.168.1.20:6369/api/music/library/3/cover"
        CoverArt.noteFailure(a, 404)
        CoverArt.noteFailure(b, 500)      // the server hiccuped: ask again later
        CoverArt.noteFailure(c, null)     // no answer at all (offline)
        assertTrue(CoverArt.isMissing(a))
        assertFalse(CoverArt.isMissing(b))
        assertFalse(CoverArt.isMissing(c))
        // Per URL: the same track on another server is its own question.
        assertFalse(CoverArt.isMissing("http://10.0.2.2:6390/api/music/library/1/cover"))
    }

    @Test fun everyLibrarySurfaceDrawsItsCoverThroughCoverImage() {
        val coverImage = "com/domovoi/app/ui/components/CoverImageKt"
        // Its Dp parameters are inline classes, so the JVM name is mangled
        // ("CoverImage-<hash>"): take whatever the compiler called it.
        val names = Class.forName(coverImage.replace('/', '.')).declaredMethods
            .map { it.name }.filter { it.startsWith("CoverImage") }.toSet()
        assertTrue(names.isNotEmpty())
        for (facade in listOf(
            "com.domovoi.app.ui.screens.music.LibraryTabKt",
            "com.domovoi.app.ui.screens.music.TrackDrawerKt",
            "com.domovoi.app.ui.screens.music.PlayerTabKt",
            "com.domovoi.app.ui.shell.player.MiniPlayerKt",
        )) {
            assertTrue(facade, Bytecode.callers(facade, coverImage, names).isNotEmpty())
        }
        for (facade in listOf(
            "com.domovoi.app.ui.screens.music.LibraryTabKt",
            "com.domovoi.app.ui.screens.music.TrackDrawerKt",
        )) {
            val asks = Bytecode.callers(facade, "com/domovoi/app/player/CoverArt", setOf("libraryPath"))
            assertTrue(facade, asks.isNotEmpty())
        }
    }

    @Test fun theMediaSessionLoadsArtworkThroughTheAppsOwnHttpClient() {
        val service = "com.domovoi.app.player.PlaybackService"
        assertTrue(
            Bytecode.callers(service, "androidx/media3/session/MediaSession\$Builder", setOf("setBitmapLoader"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers(service, "androidx/media3/datasource/okhttp/OkHttpDataSource\$Factory", setOf("<init>"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers(service, "com/domovoi/app/net/ApiClient", setOf("getHttp")).isNotEmpty(),
        )
    }

    // ── A cover that changes inside its file (V058) ─────────────────────

    /** The cover route's real headers (web/backend/api/music.py track_cover). */
    private val etag = "\"3f2a9c-c1731574800000000000\""
    private fun coverAnswer() = MockResponse()
        .setHeader("ETag", etag)
        .setHeader("Cache-Control", "public, max-age=86400")
        .setHeader("Content-Type", "image/jpeg")
        .setBody("jpeg-bytes")

    private fun fetch(server: MockWebServer, path: String, vararg extra: Interceptor): Pair<Request, okhttp3.Response> {
        val client = OkHttpClient.Builder().apply { extra.forEach { addInterceptor(it) } }.build()
        val request = Request.Builder().url(server.url(path)).build()
        val response = client.newCall(request).execute()
        response.body?.close()
        return request to response
    }

    /**
     * What Coil's disk cache does with a stored [response] when the cover is
     * drawn again: the request it sends, or null for "use the disk copy".
     * Coil's own decision (coil.network.CacheStrategy, 2.7), reached by
     * reflection because Kotlin marks it internal; a Coil upgrade that moves
     * it fails here first, which is the point.
     */
    private fun nextAsk(request: Request, response: okhttp3.Response): Request? {
        val cacheResponse = Class.forName("coil.network.CacheResponse")
        val stored = cacheResponse.getConstructor(okhttp3.Response::class.java).newInstance(response)
        val factory = Class.forName("coil.network.CacheStrategy\$Factory")
        val strategy = factory.getConstructor(Request::class.java, cacheResponse)
            .newInstance(request, stored).let { factory.getMethod("compute").invoke(it) }
        return strategy.javaClass.getMethod("getNetworkRequest").invoke(strategy) as Request?
    }

    @Test fun aKeptCoverIsAskedAboutAgainByItsEtag() {
        val server = MockWebServer().apply { start() }
        try {
            server.enqueue(coverAnswer())
            val (request, response) = fetch(server, "/api/music/library/12/cover", CoverArt.revalidate)
            assertEquals(etag, response.header("ETag"))
            assertTrue(response.cacheControl.noCache)
            val ask = nextAsk(request, response)
            // The disk copy is used only after a 304 for this ETag.
            assertEquals(etag, ask?.header("If-None-Match"))
        } finally {
            server.shutdown()
        }
    }

    @Test fun withoutItTheOldPictureWasShownForADay() {
        // The defect, as the server's headers alone made Coil behave: no
        // question asked for 24 hours, so a cover written into the file (or
        // undone) stayed old on the phone.
        val server = MockWebServer().apply { start() }
        try {
            server.enqueue(coverAnswer())
            val (request, response) = fetch(server, "/api/music/library/12/cover")
            assertNull(nextAsk(request, response))
        } finally {
            server.shutdown()
        }
    }

    @Test fun aNotModifiedAnswerStaysOneToAskAbout() {
        // Coil merges a 304's headers into the kept copy: the server's 304
        // carries max-age=86400 again, which would make the copy fresh for a day.
        val server = MockWebServer().apply { start() }
        try {
            server.enqueue(MockResponse().setResponseCode(304)
                .setHeader("ETag", etag).setHeader("Cache-Control", "public, max-age=86400"))
            val (_, response) = fetch(server, "/api/music/library/12/cover", CoverArt.revalidate)
            assertEquals(304, response.code)
            assertTrue(response.cacheControl.noCache)
        } finally {
            server.shutdown()
        }
    }

    @Test fun onlyTheCoverRouteIsTouched() {
        assertTrue(CoverArt.isCoverRoute("/api/music/library/12/cover"))
        assertFalse(CoverArt.isCoverRoute("/api/music/library/12/audio"))
        assertFalse(CoverArt.isCoverRoute("/api/podcasts/3/artwork"))
        assertFalse(CoverArt.isCoverRoute("/api/music/library/x/cover"))
        val server = MockWebServer().apply { start() }
        try {
            server.enqueue(coverAnswer())
            val (_, response) = fetch(server, "/api/podcasts/3/artwork", CoverArt.revalidate)
            assertEquals("public, max-age=86400", response.header("Cache-Control"))
        } finally {
            server.shutdown()
        }
    }

    @Test fun aReturnToTheScreenStartsANewEpoch() {
        val start = CoverArt.epoch.value
        // A first start, or a rotation (onStop with a configuration change
        // skips appBackgrounded): nothing changes.
        CoverArt.appForegrounded()
        assertEquals(start, CoverArt.epoch.value)
        val url = "http://192.168.1.20:6369/api/music/library/1/cover"
        CoverArt.noteFailure(url, 404)
        CoverArt.appBackgrounded()
        CoverArt.appForegrounded()
        assertEquals(start + 1, CoverArt.epoch.value)
        // A song can have gained a cover while the app was away.
        assertFalse(CoverArt.isMissing(url))
        // Memory is keyed by epoch, so a kept picture is fetched (and checked) again.
        assertTrue(CoverArt.memoryKey(url, start) != CoverArt.memoryKey(url, start + 1))
    }

    @Test fun theAppIsWiredToRecheckCovers() {
        assertTrue(
            Bytecode.callers("com.domovoi.app.DomovoiApplication", "com/domovoi/app/player/CoverArt", setOf("getRevalidate"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.MainActivity", "com/domovoi/app/player/CoverArt", setOf("appForegrounded"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.MainActivity", "com/domovoi/app/player/CoverArt", setOf("appBackgrounded"))
                .isNotEmpty(),
        )
        assertTrue(
            Bytecode.callers("com.domovoi.app.ui.components.CoverImageKt", "com/domovoi/app/player/CoverArt", setOf("memoryKey"))
                .isNotEmpty(),
        )
    }
}
