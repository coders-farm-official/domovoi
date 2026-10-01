package com.domovoi.app.ui.screens.local

import com.domovoi.app.AppContainer
import com.domovoi.app.data.LocalTrack
import com.domovoi.app.player.QueueWindow
import com.domovoi.app.testing.RecordingExoPlayer
import com.domovoi.app.testing.appWith
import com.domovoi.app.testing.testPlayer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.BitSet

/**
 * Tapping a song in the on-device music list (LocalMusicList's `play`) —
 * since a2e0547 the list the app falls back to whenever the saved server
 * has not answered for 10 s. It used to queue every track on the phone; it
 * now builds the queue from a [QueueWindow] around the tapped song.
 *
 * `play` is a local function of a private composable, which Kotlin compiles
 * to a private static method `LocalMusicList$play(shown, app, track)` on
 * `LocalMediaScreensKt`. The test calls that method, so it runs the app's
 * own code path from the tap to the player's queue.
 */
class LocalMusicPlayTest {

    /** The list the screen shows, recording which positions were read. */
    private class ReadList<T>(private val backing: List<T>) : AbstractList<T>() {
        val read = BitSet()
        override val size: Int get() = backing.size
        override fun get(index: Int): T {
            read.set(index)
            return backing[index]
        }
    }

    private fun track(i: Int) = LocalTrack(
        id = 10_000L + i, title = "song $i", artist = "artist", album = "album",
        durationSec = 200.0, uri = "content://media/external/audio/media/${10_000 + i}",
        albumArtUri = null,
    )

    private fun tap(shown: List<LocalTrack>, app: AppContainer, track: LocalTrack) {
        val screens = Class.forName("com.domovoi.app.ui.screens.local.LocalMediaScreensKt")
        val play = screens.declaredMethods.singleOrNull { it.name == "LocalMusicList\$play" }
            ?: error(
                "LocalMusicList's local play() is gone or renamed — point this test at " +
                    "whatever a tap on an on-device song calls now",
            )
        play.isAccessible = true
        val args = play.parameterTypes.map { type ->
            when {
                type == AppContainer::class.java -> app
                type == LocalTrack::class.java -> track
                type.isAssignableFrom(List::class.java) -> shown
                else -> error("unexpected parameter of LocalMusicList\$play: $type")
            }
        }
        play.invoke(null, *args.toTypedArray())
    }

    @Test fun tappingASongQueuesAWindowAroundIt() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        val tracks = (0 until 5_007).map(::track)

        tap(tracks, appWith(player), tracks[3_000])

        assertEquals(QueueWindow.MAX, player.queue.value.size)
        assertEquals("dev-${tracks[3_000].id}", player.current?.uid)
        assertEquals(QueueWindow.LEAD, player.index.value)
        val from = 3_000 - QueueWindow.LEAD
        assertEquals(
            tracks.subList(from, from + QueueWindow.MAX).map { "dev-${it.id}" },
            player.queue.value.map { it.uid },
        )
        assertEquals(QueueWindow.MAX, (exo.named("setMediaItems").single()[0] as List<*>).size)
    }

    @Test fun aTapReadsOnlyTheWindowNotTheWholeLibrary() {
        // Building a queue item for every track on the phone, only for the
        // player to keep 500 of them, is the main-thread work the window
        // exists to skip: past the window the list is never read.
        val tracks = ReadList((0 until 5_007).map(::track))
        val player = testPlayer()

        tap(tracks, appWith(player), tracks[100])

        val windowEnd = 100 - QueueWindow.LEAD + QueueWindow.MAX
        assertEquals("dev-${tracks[100].id}", player.current?.uid)
        assertTrue(tracks.read[windowEnd - 1])
        assertEquals(
            "the tap read the list past its queue window",
            -1, tracks.read.nextSetBit(windowEnd),
        )
    }

    @Test fun aShortListIsQueuedWholeFromTheTappedSong() {
        val player = testPlayer()
        val tracks = (0 until 20).map(::track)

        tap(tracks, appWith(player), tracks[13])

        assertEquals(tracks.map { "dev-${it.id}" }, player.queue.value.map { it.uid })
        assertEquals(13, player.index.value)
    }
}
