package com.domovoi.app.audio

import com.domovoi.app.audio.MicOwnership.Owner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/** One microphone owner at a time; room mode shuts the others out. */
class MicOwnershipTest {
    @Test
    fun oneOwnerAtATime() {
        val mic = MicOwnership()
        val chat = mic.tryAcquire(Owner.CHAT_MIC)
        assertNotNull(chat)
        assertNull(mic.tryAcquire(Owner.DROP_IN))
        assertNull(mic.tryAcquire(Owner.CHAT_MIC))
        assertEquals("The microphone is in use by the chat mic.", mic.refusal(Owner.DROP_IN))
        chat!!.close()
        assertNotNull(mic.tryAcquire(Owner.DROP_IN))
    }

    @Test
    fun roomModeShutsEveryoneElseOutUntilItEnds() {
        val mic = MicOwnership()
        val room = mic.tryAcquire(Owner.ROOM_MODE)!!
        assertTrue(mic.roomModeActive)
        for (other in listOf(Owner.DROP_IN, Owner.CHAT_MIC, Owner.ASSISTANT)) {
            assertNull(mic.tryAcquire(other))
            assertTrue(mic.refusal(other)!!.contains("room mode"))
        }
        val e = assertThrows(IOException::class.java) { mic.acquireOrThrow(Owner.ASSISTANT) }
        assertTrue(e.message!!.contains("room"))
        room.close()
        assertFalse(mic.roomModeActive)
        assertNotNull(mic.tryAcquire(Owner.ASSISTANT))
    }

    @Test
    fun roomModeDoesNotStartOverACall() {
        val mic = MicOwnership()
        val call = mic.tryAcquire(Owner.DROP_IN)!!
        assertNull(mic.tryAcquire(Owner.ROOM_MODE))
        assertFalse(mic.roomModeActive)
        call.close()
        assertNotNull(mic.tryAcquire(Owner.ROOM_MODE))
    }

    @Test
    fun aStaleLeaseClosingTwiceDoesNotFreeSomeoneElsesMic() {
        val mic = MicOwnership()
        val first = mic.tryAcquire(Owner.CHAT_MIC)!!
        first.close()
        val second = mic.tryAcquire(Owner.ASSISTANT)!!
        first.close()
        assertEquals(Owner.ASSISTANT, mic.holder())
        second.close()
        assertNull(mic.holder())
    }
}
