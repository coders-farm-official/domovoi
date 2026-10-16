package com.domovoi.app.diagnostics

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Delta review N7 (2026-10-16): no spoken text or transcript in logcat on
 *  a release build. */
class SpokenLogTest {

    @Test fun aReleaseBuildLogsOnlyHowManyWords() {
        assertEquals("<3 words>", SpokenLog.said("play some jazz", words = false))
        assertEquals("<1 word>", SpokenLog.said("  hello ", words = false))
        assertEquals("<0 words>", SpokenLog.said(null, words = false))
        assertEquals("play some jazz", SpokenLog.said("play some jazz", words = true))
    }

    @Test fun theWordsFollowTheBuildType() {
        // Unit tests run the debug variant: BuildConfig.DEBUG is true there,
        // false in every release build.
        assertEquals(com.domovoi.app.BuildConfig.DEBUG, SpokenLog.wordsAllowed)
    }

    private fun source(rel: String): String =
        listOf(File("src/main/java/com/domovoi/app/$rel"), File("app/src/main/java/com/domovoi/app/$rel"))
            .first { it.isFile }.readText()

    /** The two lines the review named: a room phone's "heard: …" and the
     *  on-device voice's sentences (as phonemes, which spell the words). */
    @Test fun theRoomPhoneAndThePhonesVoiceLogThroughIt() {
        val room = source("roommode/RoomEngine.kt")
        assertTrue("heard: \${SpokenLog.said(f.text)}" in room)
        assertFalse("\"heard: \${f.text}\"" in room)
        val piper = source("assistant/PiperSpeechOut.kt")
        assertTrue("phonemes: \${SpokenLog.said(" in piper)
    }
}
