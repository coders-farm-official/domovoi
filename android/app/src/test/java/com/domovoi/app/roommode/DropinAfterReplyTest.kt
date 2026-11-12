package com.domovoi.app.roommode

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A drop-in marked `await_ready` (docs/uml/satellite-protocol.md; the Pi's
 * satellite/tests/test_dropin_after_reply.py): the reply announcing the
 * call plays out (at most 8 s), THEN the call opens and `dropin_ready` goes
 * out; a `dropin_end`, a newer `dropin_start` or a disconnect while waiting
 * drops it; without `await_ready` the call opens at once, as before.
 *
 * A fake clock: each poll moves it on by the poll's length, and [onPoll]
 * lets a test end the reply or cancel the call at a given time.
 */
class DropinAfterReplyTest {
    private class Room {
        var clock = 0L
        var playing = false
        val log = mutableListOf<String>()
        var onPoll: (Long) -> Unit = {}
        private val queued = mutableListOf<suspend () -> Unit>()

        val dropin = DropinAfterReply(
            stillPlaying = { playing },
            sendReady = { log += "dropin_ready@$clock" },
            now = { clock },
            launch = { queued += it },
            pause = { ms -> clock += ms; onPoll(clock) },
        )

        fun enter(name: String = "call"): () -> Unit = { log += "enter $name@$clock" }

        /** Runs the waits launched so far, as the engine's scope would. */
        fun runLaunched() = runBlocking {
            while (queued.isNotEmpty()) queued.removeAt(0)()
        }
    }

    @Test
    fun withoutAwaitReadyTheCallOpensAtOnceAndNoReadyIsSent() {
        val r = Room()
        r.playing = true // even over a reply: an older core sends the call's audio at once
        r.dropin.start(awaitReady = false, enter = r.enter())
        r.runLaunched()
        assertEquals(listOf("enter call@0"), r.log)
    }

    @Test
    fun withNothingPlayingTheCallOpensAtOnceThenReady() {
        val r = Room()
        r.dropin.start(awaitReady = true, enter = r.enter())
        assertEquals(listOf("enter call@0", "dropin_ready@0"), r.log)
        assertFalse(r.dropin.waiting)
    }

    @Test
    fun theReplyPlaysOutThenTheCallOpensThenReady() {
        val r = Room()
        r.playing = true
        r.onPoll = { t -> if (t >= 3_000) r.playing = false }
        r.dropin.start(awaitReady = true, enter = r.enter())
        assertTrue("nothing opens while the reply plays", r.log.isEmpty())
        assertTrue(r.dropin.waiting)
        r.runLaunched()
        assertEquals(listOf("enter call@3000", "dropin_ready@3000"), r.log)
        assertFalse(r.dropin.waiting)
    }

    @Test
    fun aReplyThatNeverEndsOpensTheCallAtTheEightSecondCap() {
        val r = Room()
        r.playing = true
        r.dropin.start(awaitReady = true, enter = r.enter())
        r.runLaunched()
        assertEquals(listOf("enter call@8000", "dropin_ready@8000"), r.log)
        assertEquals(8_000L, DropinAfterReply.CAP_MS)
    }

    @Test
    fun aDropinEndWhileWaitingDropsTheCall() {
        val r = Room()
        r.playing = true
        r.onPoll = { t ->
            if (t == 1_000L) r.dropin.cancel() // dropin_end
            if (t >= 2_000) r.playing = false
        }
        r.dropin.start(awaitReady = true, enter = r.enter())
        r.runLaunched()
        assertEquals(emptyList<String>(), r.log)
        assertFalse(r.dropin.waiting)
    }

    @Test
    fun aDisconnectAfterTheReplyEndedStillDropsTheCall() {
        // The reply ends and the session drops on the same poll: no call
        // opens and no dropin_ready goes to the next session.
        val r = Room()
        r.playing = true
        r.onPoll = { t -> if (t >= 500) { r.playing = false; r.dropin.cancel() } }
        r.dropin.start(awaitReady = true, enter = r.enter())
        r.runLaunched()
        assertEquals(emptyList<String>(), r.log)
    }

    @Test
    fun aNewerDropinStartReplacesTheWaitingOne() {
        val r = Room()
        r.playing = true
        r.onPoll = { t ->
            if (t == 1_000L) r.dropin.start(awaitReady = true, enter = r.enter("second"))
            if (t >= 2_000) r.playing = false
        }
        r.dropin.start(awaitReady = true, enter = r.enter("first"))
        r.runLaunched()
        assertEquals(listOf("enter second@2000", "dropin_ready@2000"), r.log)
    }

    @Test
    fun aNewerStartWithoutAwaitReadyAlsoDropsTheWaitingOne() {
        val r = Room()
        r.playing = true
        r.onPoll = { t ->
            if (t == 1_000L) r.dropin.start(awaitReady = false, enter = r.enter("second"))
            if (t >= 2_000) r.playing = false
        }
        r.dropin.start(awaitReady = true, enter = r.enter("first"))
        r.runLaunched()
        assertEquals(listOf("enter second@1000"), r.log)
    }

    @Test
    fun aCallOpensOnceEvenIfAskedTwice() {
        val r = Room()
        r.playing = true
        val n = r.dropin.start(awaitReady = true, enter = r.enter())
        r.playing = false
        assertTrue(r.dropin.open(n, r.enter()))
        assertFalse(r.dropin.open(n, r.enter()))
        r.runLaunched()
        assertEquals(listOf("enter call@0", "dropin_ready@0"), r.log)
    }
}
