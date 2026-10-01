package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.lazy.LazyItemScope
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableIntStateOf
import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayTarget
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The 2026-09-30 freeze and crash: the player tab put the whole queue inside
 * ONE lazy item, so every row was built at once. These tests record what the
 * player and room-queue tabs hand the LazyColumn and fail if a long list
 * goes back to being drawn in a single item.
 */
class LazyTabsTest {

    /** Records the item calls a tab makes, without composing anything. */
    private class RecordingScope : LazyListScope {
        val blocks = mutableListOf<Pair<String, Int>>()
        val keys = mutableListOf<Any?>()

        override fun item(key: Any?, contentType: Any?, content: @Composable LazyItemScope.() -> Unit) {
            blocks += "item" to 1
            keys += key
        }

        override fun items(
            count: Int,
            key: ((index: Int) -> Any)?,
            contentType: (index: Int) -> Any?,
            itemContent: @Composable LazyItemScope.(index: Int) -> Unit,
        ) {
            blocks += "items" to count
            repeat(count) { keys += key?.invoke(it) }
        }

        @ExperimentalFoundationApi
        override fun stickyHeader(key: Any?, contentType: Any?, content: @Composable LazyItemScope.() -> Unit) {
            item(key, contentType, content)
        }
    }

    private fun track(i: Int) = PlayItem.fromTrack(i.toLong(), "track $i", "artist", null, 180.0)

    private fun model(
        queue: List<PlayItem>,
        index: Int = 0,
        room: PlayTarget.Room? = null,
        chapters: List<Chapter> = emptyList(),
    ) = PlayerTabModel(queue, index, room, chapters, mutableIntStateOf(0), hasLibraryItems = queue.isNotEmpty())

    @Test fun everyQueueEntryIsItsOwnLazyItem() {
        val queue = (0 until 5_007).map(::track) + track(3) // a track queued twice
        val chapters = (0 until 40).map { Chapter("c$it", it * 60.0) }
        val scope = RecordingScope()
        scope.playerTab(model(queue, index = 2_000, chapters = chapters), listOf("office"), onSaveQueue = {})

        // Head, chapters label, queue label: the only single items.
        assertEquals(3, scope.blocks.count { it.first == "item" })
        assertTrue(scope.blocks.contains("items" to chapters.size))
        assertTrue(scope.blocks.contains("items" to queue.size))
        // Keys are present and unique, or the LazyColumn throws.
        assertTrue(scope.keys.all { it != null })
        assertEquals(scope.keys.size, scope.keys.toSet().size)
    }

    @Test fun nothingQueuedIsOneEmptyState() {
        val scope = RecordingScope()
        scope.playerTab(model(emptyList()), emptyList(), onSaveQueue = {})
        assertEquals(listOf("item" to 1), scope.blocks)
        assertEquals(listOf<Any?>("player-empty"), scope.keys)
    }

    @Test fun castingWithAnEmptyLocalQueueStillShowsTheHead() {
        val scope = RecordingScope()
        scope.playerTab(model(emptyList(), room = PlayTarget.Room("office")), listOf("office"), onSaveQueue = {})
        assertEquals(listOf<Any?>("player-head", "player-queue-label", "player-queue-empty"), scope.keys)
    }

    @Test fun everyRoomQueueEntryIsItsOwnLazyItem() {
        val items = (0 until 800).map { QueueItem(songId = it.toLong(), pos = it, title = "song $it") }
        val rq = RoomQueueModel(
            sel = RoomQueueSelection("office"),
            queue = RoomQueue(roomId = "office", items = items),
            loading = false,
            remove = {},
            move = { _, _ -> },
            clear = {},
        )
        val scope = RecordingScope()
        scope.queueTab(listOf("office", "kitchen"), rq)
        assertEquals(listOf("item" to 1, "items" to 800), scope.blocks)
        assertEquals(scope.keys.size, scope.keys.toSet().size)
    }

    @Test fun roomQueueWithoutRoomsIsTheHeaderAlone() {
        val rq = RoomQueueModel(RoomQueueSelection(null), RoomQueue(), false, {}, { _, _ -> }, {})
        val scope = RecordingScope()
        scope.queueTab(emptyList(), rq)
        assertEquals(listOf<Any?>("room-queue-head"), scope.keys)
    }
}
