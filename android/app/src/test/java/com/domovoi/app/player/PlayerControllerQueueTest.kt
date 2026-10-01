package com.domovoi.app.player

import androidx.media3.common.MediaItem
import com.domovoi.app.testing.RecordingExoPlayer
import com.domovoi.app.testing.testPlayer
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The 2026-09-30 freeze and crash: tapping a song in the on-device list
 * queued every track on the phone, and with a few thousand of them starting
 * playback stalled the main thread (a MediaItem each, all published by the
 * media session) and the player tab later ran the app out of memory.
 * [PlayerController.playItems] caps whatever it is handed to a
 * [QueueWindow] around the tapped item — these tests pin that call site, not
 * just the helper (QueueWindowTest).
 */
class PlayerControllerQueueTest {

    private fun deviceTrack(i: Int) = PlayItem.fromDeviceAudio(
        i.toLong(), "song $i", "artist", "album", 180.0,
        "content://media/external/audio/media/$i", null,
    )

    @Test fun aLongListIsQueuedAsAWindowAroundTheTappedItem() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        val items = (0 until 5_007).map(::deviceTrack)

        player.playItems(items, startIndex = 3_000)

        assertEquals("the whole list was queued", QueueWindow.MAX, player.queue.value.size)
        val from = 3_000 - QueueWindow.LEAD
        assertEquals(items.subList(from, from + QueueWindow.MAX), player.queue.value)
        assertEquals(QueueWindow.LEAD, player.index.value)
        assertEquals(items[3_000], player.current)

        // The engine gets the same window, starting at the tapped item.
        val (mediaItems, startIndex, startMs) = exo.named("setMediaItems").single()
        mediaItems as List<*>
        assertEquals(QueueWindow.MAX, mediaItems.size)
        assertEquals(QueueWindow.LEAD, startIndex)
        assertEquals(0L, startMs)
        assertEquals(items[3_000].uid, (mediaItems[QueueWindow.LEAD] as MediaItem).mediaId)
        assertEquals(items[from].uid, (mediaItems.first() as MediaItem).mediaId)
        assertEquals(listOf("setMediaItems", "prepare", "play"),
            exo.calls.map { it.first }.filter { it in setOf("setMediaItems", "prepare", "play") })
    }

    @Test fun aTapNearTheEndFillsTheWindowFromBefore() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        val items = (0 until 2_000).map(::deviceTrack)

        player.playItems(items, startIndex = 1_990)

        assertEquals("the whole list was queued", QueueWindow.MAX, player.queue.value.size)
        assertEquals(items.takeLast(QueueWindow.MAX), player.queue.value)
        assertEquals(items[1_990], player.current)
        assertEquals(QueueWindow.MAX, (exo.named("setMediaItems").single()[0] as List<*>).size)
    }

    @Test fun aShortListIsQueuedWhole() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        val items = (0 until 12).map(::deviceTrack)

        player.playItems(items, startIndex = 7, resumeSec = 2.5)

        assertEquals(items, player.queue.value)
        assertEquals(7, player.index.value)
        val (mediaItems, startIndex, startMs) = exo.named("setMediaItems").single()
        assertEquals(12, (mediaItems as List<*>).size)
        assertEquals(7, startIndex)
        assertEquals(2_500L, startMs)
    }
}
