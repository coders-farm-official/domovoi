package com.domovoi.app.ui.screens.chat

import com.domovoi.app.assistant.ModelCatalog
import com.domovoi.app.voice.PhoneVoiceChoice
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Which voice the chat mic's reply is heard in (Kamron's test, 2026-10-12:
 * Settings → Voice on Kristin, downloaded, and the reply still came in
 * the server's Norman). The phone's own pick, downloaded: spoken on the
 * phone from the reply text, and the server is asked for no audio.
 * "Same as the server", or a pick not downloaded: the server's audio.
 */
class ReplyPlaybackTest {
    private val kristin = ModelCatalog.kristin
    private val same = PhoneVoiceChoice.SAME_AS_SERVER

    @Test fun theOwnPickDownloadedSpeaksOnThePhone() {
        assertEquals(kristin, PhoneVoiceChoice.replyVoiceOnPhone(kristin.id) { it == kristin })
    }

    @Test fun sameAsTheServerPlaysTheServersAudio() {
        // Even with every voice downloaded: it is the same voice anyway.
        assertNull(PhoneVoiceChoice.replyVoiceOnPhone(same) { true })
        assertNull(PhoneVoiceChoice.replyVoiceOnPhone(null) { true })
    }

    @Test fun anOwnPickNotDownloadedPlaysTheServersAudio() {
        assertNull(PhoneVoiceChoice.replyVoiceOnPhone(kristin.id) { false })
        assertNull(PhoneVoiceChoice.replyVoiceOnPhone(kristin.id) { it == ModelCatalog.norman })
        assertNull(PhoneVoiceChoice.replyVoiceOnPhone("nonsense") { true })
    }

    @Test fun thePathOfAReply() {
        // Own voice ready: the phone speaks, even over audio an older server sent anyway.
        assertEquals(ReplyPlayback.PHONE_VOICE, replyPlayback(ownVoiceReady = true, serverAudio = false, reply = "It's 9:05."))
        assertEquals(ReplyPlayback.PHONE_VOICE, replyPlayback(ownVoiceReady = true, serverAudio = true, reply = "It's 9:05."))
        assertEquals(ReplyPlayback.SERVER_AUDIO, replyPlayback(ownVoiceReady = false, serverAudio = true, reply = "It's 9:05."))
        assertEquals(ReplyPlayback.NONE, replyPlayback(ownVoiceReady = false, serverAudio = false, reply = "It's 9:05."))
        // Nothing to say: nothing played.
        assertEquals(ReplyPlayback.NONE, replyPlayback(ownVoiceReady = true, serverAudio = false, reply = " "))
    }
}
