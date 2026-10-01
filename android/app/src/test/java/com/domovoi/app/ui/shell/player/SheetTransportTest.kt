package com.domovoi.app.ui.shell.player

import androidx.compose.runtime.CompositionLocalProvider
import com.domovoi.app.LocalApp
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.RecordingExoPlayer
import com.domovoi.app.testing.appWith
import com.domovoi.app.testing.button
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.testPlayer
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The mini player's queue sheet: its previous button takes a tap while
 * casting (the room's previous, PlayerController.prev). It was greyed out
 * while casting until rooms had a previous; on 2026-10-01 it was made live,
 * and only the emulator ever checked the player tab's button — nothing
 * checked the sheet's.
 *
 * The sheet itself is a dialog window, which can't be composed on the JVM,
 * so its transport row ([SheetTransport]) is composed alone, against a real
 * PlayerController that is casting to office.
 */
class SheetTransportTest {

    @Suppress("UNCHECKED_CAST")
    private fun <T> writable(flow: StateFlow<T>) = flow as MutableStateFlow<T>

    @Test fun previousInTheSheetTakesATapWhileCasting() {
        val exo = RecordingExoPlayer()
        val player = testPlayer(exo)
        writable(player.target).value = PlayTarget.Room("office")
        val app = appWith(player)
        val tree = NodeTree()
        var enabled = emptyMap<String, Boolean>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(LocalApp provides app) {
                            SheetTransport(playing = true, onDismiss = {})
                        }
                    }
                }
                settle()
                enabled = listOf("previous", "play/pause", "next", "stop").associateWith { tree.button(it).enabled }
                // The tap is the player's previous: on this phone (no room to
                // reach in a unit test) it moves the phone's own queue back.
                writable(player.target).value = PlayTarget.Local
                tree.button("previous").click()
            }
        }
        assertEquals(mapOf("previous" to true, "play/pause" to true, "next" to true, "stop" to true), enabled)
        assertTrue(exo.calls.toString(), exo.named("seekToPreviousMediaItem").isNotEmpty())
    }
}
