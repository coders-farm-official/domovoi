package com.domovoi.app.ui.shell.player

import com.domovoi.app.testing.Bytecode
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The mini player's queue sheet lists the whole local queue. While
 * something plays, the position ticks every 500 ms; the sheet used to
 * collect it at its top level, so every tick rebuilt the sheet and its
 * queue list. The seek bar (`SheetSeek`) is now the only composable in the
 * sheet that collects the position and duration, so a tick recomposes the
 * seek bar alone.
 *
 * The sheet is a ModalBottomSheet — a dialog window, which cannot be
 * composed on the JVM — so this reads the compiled sheet instead: which
 * composables call `positionSec` / `durationSec` on the player. A state
 * read recomposes the composable that made it, and nothing else.
 */
class PlayerQueueSheetSeekTest {

    private val readers = Bytecode.callers(
        fileFacade = "com.domovoi.app.ui.shell.player.PlayerQueueSheetKt",
        owner = "com/domovoi/app/player/PlayerController",
        names = setOf("getPositionSec", "getDurationSec"),
    )

    private fun Bytecode.Call.inSheetSeek() =
        method == "SheetSeek" || method.startsWith("SheetSeek$") || className.contains("\$SheetSeek")

    @Test fun onlyTheSeekBarFollowsThePositionTick() {
        assertEquals(
            "something in the queue sheet besides SheetSeek collects the playback position",
            emptyList<Bytecode.Call>(),
            readers.filterNot { it.inSheetSeek() },
        )
    }

    @Test fun theSeekBarStillFollowsIt() {
        // Not vacuous: the reader finds the seek bar's own collection.
        assertTrue(readers.any { it.inSheetSeek() && it.method == "SheetSeek" })
    }
}
