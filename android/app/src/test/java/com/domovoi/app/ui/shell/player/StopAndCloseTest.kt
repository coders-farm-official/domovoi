package com.domovoi.app.ui.shell.player

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.LocalApp
import com.domovoi.app.LocalToast
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.appWith
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.field
import com.domovoi.app.testing.libraryQueue
import com.domovoi.app.testing.setField
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.coroutines.coroutineContext

/**
 * The mini player's "×" (handoff §2b, the web's "Stop and close" in
 * web/static/player.jsx): it stops, empties the queue and takes the bar
 * away — and while casting it stops the ROOM first. Before, the Android "×"
 * ("stop") emptied the queue on the phone, but while casting it only sent
 * the room a stop and left the bar up, still "casting to" the room.
 *
 * A real controller on a MockWebServer ([CastRig]) under the real
 * [MiniPlayer], composed headless; the button is tapped as a person would.
 */
class StopAndCloseTest {
    private val rig = CastRig()
    private val player get() = rig.player
    // No cover: the bar's cover image loads through Coil, which needs the
    // main thread a JVM test has not got. The cover is not what is tested.
    private val queue = libraryQueue("Stone Floor", "Old Barrels", "Quiet Jars").map { it.copy(coverPath = null) }

    @After fun tearDown() = rig.close()

    private fun castingToOffice() {
        player.playItems(queue, 1)
        rig.room("office", "play", "Old Barrels", 7.0)
        runBlocking { player.castTo("office") }
        rig.awaitRemote("office")
        rig.log.clear()
    }

    /** The mini player, tapped on "Stop and close"; what it shows after,
     *  and what it toasted. */
    private fun tapStopAndClose(): Pair<List<String?>, List<String>> {
        val toasts = mutableListOf<String>()
        var after: List<String?> = emptyList()
        withHeadlessWindow {
            val tree = NodeTree()
            composeTest(tree) {
                val app = appWith(player)
                setField(app, "scope", CoroutineScope(coroutineContext))
                setField(app, "api", field(player, "api"))
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(LocalApp provides app, LocalToast provides { toasts += it }) {
                            MiniPlayer(onOpenPlayer = {})
                        }
                    }
                }
                // A casting bar's "live" dot never stops animating: frames, not quiet.
                settle(untilQuiet = false, frames = 30)
                tree.button("Stop and close").click()
                // The tap's work runs on this loop; the room's answer comes
                // from the MockWebServer's thread.
                val until = System.currentTimeMillis() + 5_000
                while (System.currentTimeMillis() < until && (player.queue.value.isNotEmpty() || player.target.value is PlayTarget.Room) && toasts.isEmpty()) {
                    delay(10)
                }
                settle(untilQuiet = false, frames = 30)
                after = tree.buttons().map { it.label }
            }
        }
        return after to toasts
    }

    @Test fun onThePhoneItStopsEmptiesTheQueueAndTakesTheBarAway() {
        player.playItems(queue, 1)
        rig.log.clear()

        val (after, toasts) = tapStopAndClose()

        assertEquals(emptyList<Any>(), player.queue.value)
        assertFalse("the phone still plays", rig.exo.playing)
        assertTrue("the phone's player still holds the queue", rig.exo.player.mediaItemCount == 0)
        assertEquals("the bar is still there: $after", emptyList<String?>(), after)
        assertEquals("no room was told anything", emptyList<String>(), rig.actions().filter { it.startsWith("POST") })
        assertEquals(emptyList<String>(), toasts)
    }

    @Test fun whileCastingItStopsTheRoomThenClosesTheBar() {
        castingToOffice()

        val (after, toasts) = tapStopAndClose()

        assertTrue(
            "the room was not stopped: ${rig.actions()}",
            rig.actions().any { it.startsWith("POST /api/music/stop/office") },
        )
        assertEquals(PlayTarget.Local, player.target.value)
        assertEquals(null, player.remote.value)
        assertEquals(emptyList<Any>(), player.queue.value)
        assertEquals("the bar is still there: $after", emptyList<String?>(), after)
        assertEquals(emptyList<String>(), toasts)
        // Nothing watches the room any more (the poll reads every 2 s): a
        // read already on its way may land, none after it.
        Thread.sleep(300)
        val reads = rig.log.count { it.startsWith("GET /api/music/now-playing") }
        Thread.sleep(2_500)
        assertEquals("the room is still polled", reads, rig.log.count { it.startsWith("GET /api/music/now-playing") })
    }

    @Test fun aRoomThatRefusesTheStopKeepsTheBarAndSaysSo() {
        castingToOffice()
        rig.failing["/api/music/stop/office"] = 502

        val (after, toasts) = tapStopAndClose()

        assertEquals(listOf("couldn't stop the room — the player is still open"), toasts)
        assertEquals(PlayTarget.Room("office"), player.target.value)
        assertEquals(3, player.queue.value.size)
        assertTrue("the bar went away: $after", "Stop and close" in after)
    }

    @Test fun aRoomThatSaysItDidNotStopKeepsTheBarToo() {
        castingToOffice()
        rig.answers["/api/music/stop/office"] = """{"ok":false}"""

        val (_, toasts) = tapStopAndClose()

        assertEquals(listOf("couldn't stop the room — the player is still open"), toasts)
        assertEquals(PlayTarget.Room("office"), player.target.value)
    }
}
