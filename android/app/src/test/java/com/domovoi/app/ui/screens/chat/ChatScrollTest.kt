package com.domovoi.app.ui.screens.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.sign

/**
 * The chat's drags at a list's end (2026-10-12, "my scroll seems to be
 * locked"): the conversation's stretch when it fits, and the voice view's
 * dock, which the platform's stretch used to starve.
 */
class ChatScrollTest {

    /**
     * One drag frame at the end of a list, in Compose foundation 1.7.6's
     * order (AndroidEdgeEffectOverscrollEffect.applyToScroll): a stretch
     * already pulled in the drag's direction takes the delta FIRST; otherwise
     * the list (at its end, so it scrolls nothing) hands the delta to its
     * nested-scroll parent, and whatever the parent leaves over pulls the
     * stretch. Returns what the parent saw this frame.
     */
    private class ListAtItsEnd(private val parent: (Float) -> Float) {
        var stretch = 0f

        fun frame(dy: Float): Float {
            if (stretch != 0f && sign(stretch) == sign(dy)) {
                stretch += dy
                return 0f
            }
            val kept = parent(dy)
            stretch += dy - kept
            return dy
        }
    }

    private val threshold = 147f        // 56dp at the emulator's 2.625 density
    private val frame = -20f            // a finger pushing up, 20px a frame

    @Test fun aConversationThatFitsGetsTheStretchAndOneThatScrollsDrawsItsOwn() {
        assertTrue(fitStretches(canScrollBackward = false, canScrollForward = false))
        assertFalse(fitStretches(canScrollBackward = true, canScrollForward = false))
        assertFalse(fitStretches(canScrollBackward = false, canScrollForward = true))
        assertFalse(fitStretches(canScrollBackward = true, canScrollForward = true))
    }

    @Test fun aConnectionThatKeepsNothingSeesOneFrameAndTheDockNeverOpens() {
        // The connection as it was until 2026-10-12: it counted the leftover
        // and returned zero, so the stretch took it, and then every frame after.
        var counted = 0f
        val list = ListAtItsEnd { dy -> counted += dy; 0f }
        repeat(30) { list.frame(frame) }
        assertEquals(frame, counted, 0f)                    // one frame of 30
        assertFalse(dockExpanded(expanded = false, pulledSoFar = counted, thresholdPx = threshold))
    }

    @Test fun pushingOnPastTheEndOfAClosedDockOpensIt() {
        val pull = DockPull(threshold)
        val list = ListAtItsEnd { dy -> pull.leftover(pull.expanded, dy) }
        var frames = 0
        while (!pull.expanded && frames < 30) {
            list.frame(frame)
            frames++
        }
        assertTrue(pull.expanded)
        assertEquals(8, frames)                            // 8 x 20px passes 147px
        assertEquals("no stretch builds while the dock takes the pull", 0f, list.stretch, 0f)
    }

    @Test fun pullingPastTheTopOfTheOpenThreadClosesIt() {
        val pull = DockPull(threshold)
        pull.leftover(expandedNow = true, leftoverY = 0f)
        val list = ListAtItsEnd { dy -> pull.leftover(pull.expanded, dy) }
        repeat(8) { list.frame(-frame) }
        assertFalse(pull.expanded)
    }

    @Test fun theOtherDirectionIsLeftToTheStretch() {
        val pull = DockPull(threshold)
        // A closed dock pulled down past its top: nothing kept, it stays closed.
        assertEquals(0f, pull.leftover(expandedNow = false, leftoverY = 400f), 0f)
        assertFalse(pull.expanded)
        // An open thread pushed on past its end: nothing kept, it stays open.
        assertEquals(0f, pull.leftover(expandedNow = true, leftoverY = -400f), 0f)
        assertTrue(pull.expanded)
    }

    @Test fun liftingTheFingerStartsTheCountAgain() {
        val pull = DockPull(threshold)
        assertEquals(-100f, pull.leftover(expandedNow = false, leftoverY = -100f), 0f)
        pull.lift()
        pull.leftover(expandedNow = false, leftoverY = -100f)
        assertFalse("two short pushes are not one long one", pull.expanded)
        pull.leftover(expandedNow = false, leftoverY = -60f)
        assertTrue(pull.expanded)
    }
}
