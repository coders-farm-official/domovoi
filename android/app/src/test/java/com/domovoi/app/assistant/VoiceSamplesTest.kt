package com.domovoi.app.assistant

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceSamplesTest {
    private val clock = TestClock()

    @Test fun everyLineIsReadInOrderAndTimed() = runBlocking {
        val voice = FakeSpeechOut(clock, synthMs = 120)
        val seen = mutableListOf<Int>()
        val spoken = mutableListOf<VoiceSampleResult>()
        val results = runVoiceSamples(voice, VoiceSamples.LINES, gapMs = 0, onLine = { i, _ -> seen += i }, onSpoken = { spoken += it })
        assertEquals(VoiceSamples.LINES, voice.spoken)
        assertEquals(VoiceSamples.LINES.indices.toList(), seen)
        assertEquals(results, spoken)
        assertTrue(results.all { it.firstWordMs == 120L && it.totalMs == 120L && it.audioS == 1.0 })
    }

    @Test fun aStopEndsItBetweenLinesAndKeepsWhatWasRead() = runBlocking {
        val voice = FakeSpeechOut(clock)
        var stop = false
        val spoken = mutableListOf<VoiceSampleResult>()
        val results = runVoiceSamples(voice, VoiceSamples.LINES, stop = { stop }, gapMs = 0,
            onSpoken = { spoken += it; if (spoken.size == 2) stop = true })
        assertEquals(2, results.size)
        assertEquals(VoiceSamples.LINES.take(2), voice.spoken)
    }

    @Test fun theLinesAreTheComparisonSet() {
        assertEquals(13, VoiceSamples.LINES.size)
        assertEquals("Okay, I set a timer for 12 minutes.", VoiceSamples.LINES.first())
        assertTrue(VoiceSamples.LINES.contains("Playing \$uicideboy\$, then Beyoncé, then AC/DC."))
        assertTrue(VoiceSamples.LINES.contains("That's \$14.99, about 20% off the usual price."))
    }

    @Test fun theReportNamesTheVoiceAndListsTheSamples() {
        val device = DeviceFacts("Acme", "Phone 9", null, "16", 36, 8_000_000_000, false, "arm64-v8a", "1.0.0")
        val results = BenchResults(
            voice = "Android TextToSpeech, engine Speech Services (com.example.tts); voice en-us-x-abc-local",
            samples = listOf(
                VoiceSampleResult(VoiceSamples.LINES[0], 210, 2_400, 2.1),
                VoiceSampleResult(VoiceSamples.LINES[4], 190, 1_900, 0.0),
                VoiceSampleResult("an unknown line", null, 50, 0.0),
            ),
        )
        assertFalse(results.isEmpty)
        val text = renderBenchReport(device, results, "now")
        listOf(
            "listening: sherpa-onnx 1.13.8 built without text to speech, on ONNX Runtime 1.28.0; speaking: Android TextToSpeech",
            "voice: Android TextToSpeech, engine Speech Services (com.example.tts); voice en-us-x-abc-local",
            "VOICE SAMPLES",
            "median to the first word: 200 ms",
            "1. first word 210 ms, done 2400 ms, 2.1 s of speech: Okay, I set a timer for 12 minutes.",
            "5. first word 190 ms, done 1900 ms: Hey Domovoi!",
            "-. first word —, done 50 ms: an unknown line",
        ).forEach { assertTrue("missing: $it\n---\n$text", text.contains(it)) }
    }
}
