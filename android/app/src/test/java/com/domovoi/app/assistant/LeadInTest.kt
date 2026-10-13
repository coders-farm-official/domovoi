package com.domovoi.app.assistant

import com.domovoi.app.phonemizer.PhonemeIds
import com.domovoi.app.phonemizer.RepoPhonemizer
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

/** The cut on the numbers domovoi/tests/test_tts_lead_in.py uses, and the
 *  phone's engine saying a short sentence after the lead-in. */
class LeadInTest {
    /** 1,000 Hz audio from (seconds, level) parts. */
    private fun shape(vararg parts: Pair<Double, Float>): FloatArray =
        parts.flatMap { (s, v) -> List(Math.round(s * 1000).toInt()) { v } }.toFloatArray()

    @Test fun theCutKeepsWhatFollowsTheLeadInsPause() {
        val out = LeadIn.cut(shape(2.0 to 0.5f, 0.3 to 0f, 0.5 to 0.5f), 1000, 2.5)
        assertNotNull(out)
        assertEquals(600, out!!.size)
        assertEquals(0f, out[0])
        assertEquals(0.5f, out[200])
    }

    @Test fun theCutIgnoresAPauseInsideTheLeadInAndTrailingSilence() {
        val audio = shape(0.5 to 0.5f, 0.4 to 0f, 1.1 to 0.5f, 0.3 to 0f, 0.5 to 0.5f, 0.5 to 0f)
        assertEquals(1100, LeadIn.cut(audio, 1000, 2.5)!!.size)
        assertNull(LeadIn.cut(shape(2.0 to 0.5f, 0.5 to 0f), 1000, 2.5))
    }

    @Test fun aPauseShorterThanTheLeadInsEndIsNotIt() {
        assertNull(LeadIn.cut(shape(2.0 to 0.5f, 0.1 to 0f, 0.5 to 0.5f), 1000, 2.5))
    }

    @Test fun phonemeCount() {
        assertEquals(7, LeadIn.phonemeCount("kˈaməɹən."))
        assertEquals(11, LeadIn.phonemeCount("hˈaɪ, aɪm nˈɔːmən."))
        assertEquals(0, LeadIn.phonemeCount(""))
    }

    /** A voice that runs away: a short input alone is 3 s; the lead-in
     *  alone 1 s; the lead-in then a sentence 0.9 s, a 0.4 s pause, 0.3 s. */
    private class RunawayVoice(private val leadIds: IntArray) : VoiceModel {
        override val sampleRate = 1000
        val calls = mutableListOf<List<Int>>()

        override fun synthesize(ids: IntArray): FloatArray {
            calls += ids.toList()
            val body = leadIds.toList().dropLast(1)
            return when {
                ids.contentEquals(leadIds) -> FloatArray(1000) { 0.5f }
                ids.size > body.size && ids.toList().subList(0, body.size) == body ->
                    FloatArray(1600) { if (it < 900 || it >= 1300) 0.5f else 0f }
                ids.size < 60 -> FloatArray(3000) { if (it % 2 == 0) 0.4f else -0.4f }
                else -> FloatArray(1000) { 0.25f }
            }
        }

        override fun cancel() {}
        override fun close() {}
    }

    @Test fun aVoiceThatRunsAwaySaysAShortSentenceAfterTheLeadIn() = runBlocking {
        val phonemizer = RepoPhonemizer.withoutModel()
        val lead = phonemizer.phonemize(LeadIn.TEXT).joinToString(" ")
        val voice = RunawayVoice(PhonemeIds.ids(lead, phonemizer.idMap))
        val logs = mutableListOf<String>()
        val out = PiperSpeechOut(phonemizer, voice, "en_US-norman-medium", NullSink(), log = { logs += it })

        val audio = out.synthesize("Cameron.").samples
        val probes = LeadIn.PROBES.map { PhonemeIds.ids(phonemizer.phonemize(it).joinToString(" "), phonemizer.idMap).toList() }
        val said = PhonemeIds.ids("$lead ${phonemizer.phonemize("Cameron.").single()}", phonemizer.idMap).toList()
        assertEquals(probes + listOf(PhonemeIds.ids(lead, phonemizer.idMap).toList(), said), voice.calls)
        // the sentence and 0.1 s of the pause before it, not the 3 s alone
        assertEquals(400, audio.size)
        assertTrue(audio.take(90).all { abs(it) < 1e-6f })
        assertEquals(1f, audio.last(), 1e-6f)
        assertTrue(logs.any { "said after a lead-in" in it })

        // a long sentence is said alone, and the voice is checked only once
        voice.calls.clear()
        out.synthesize("The kitchen light is on, and the music is paused in the living room.")
        assertEquals(1, voice.calls.size)
        assertEquals(1000, out.synthesize("The kitchen light is on, and the music is paused in the living room.").samples.size)
    }

    private class NullSink : PcmSink {
        override val sampleRate = 1000
        override val tailFrames = 0
        override fun start() {}
        override fun write(pcm: ShortArray) {}
        override fun playedFrames() = 0L
        override fun stop() {}
        override fun close() {}
    }
}
