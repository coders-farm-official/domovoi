package com.domovoi.app.player

import com.domovoi.app.net.ApiClient
import com.domovoi.app.testing.Bytecode
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

    private val api = ApiClient(baseUrlProvider = { "http://192.168.0.117:6369" })

    @After fun clean() = CoverArt.forgetMisses()

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
            "http://192.168.0.117:6369/api/music/library/12/cover",
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
        val a = "http://192.168.0.117:6369/api/music/library/1/cover"
        val b = "http://192.168.0.117:6369/api/music/library/2/cover"
        val c = "http://192.168.0.117:6369/api/music/library/3/cover"
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
}
