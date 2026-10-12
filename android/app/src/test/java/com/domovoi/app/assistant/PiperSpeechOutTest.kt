package com.domovoi.app.assistant

import com.domovoi.app.phonemizer.PhonemeIds
import com.domovoi.app.phonemizer.RepoPhonemizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** A voice that "synthesizes" [samplesPerCall] samples of 0.5, or waits
 *  on [gate] first (to stop it mid-sentence). */
private class FakeVoiceModel(
    override val sampleRate: Int = 1000,
    private val samplesPerCall: Int = 500,
    private val gate: CountDownLatch? = null,
) : VoiceModel {
    val calls = mutableListOf<IntArray>()
    @Volatile var cancels = 0
    @Volatile private var cancelled = false
    val started = CountDownLatch(1)

    override fun synthesize(ids: IntArray): FloatArray {
        synchronized(calls) { calls += ids }
        started.countDown()
        if (gate != null) {
            gate.await(5, TimeUnit.SECONDS)
            if (cancelled) throw VoiceModel.Stopped()
        }
        return FloatArray(samplesPerCall) { 0.5f }
    }

    override fun cancel() {
        cancels++
        cancelled = true
        gate?.countDown()
    }

    override fun close() {}
}

/** A speaker that plays everything the moment it is written. */
private class FakeSink(override val sampleRate: Int = 1000) : PcmSink {
    val writes = mutableListOf<ShortArray>()
    var starts = 0
    var stops = 0
    private var played = 0L
    override val tailFrames = 7

    override fun start() {
        starts++
    }

    override fun write(pcm: ShortArray) {
        writes += pcm
        played += pcm.size
    }

    override fun playedFrames(): Long = played

    override fun stop() {
        stops++
        played = 0
    }

    override fun close() {}
}

class PiperSpeechOutTest {
    private val phonemizer = RepoPhonemizer.withoutModel()
    private val clock = TestClock()

    @Test fun eachSentenceIsSynthesizedAndPlayedInOrderWithPipersGap() = runBlocking {
        val voice = FakeVoiceModel()
        val sink = FakeSink()
        val out = PiperSpeechOut(phonemizer, voice, "en_US-norman-medium", sink, clock = clock.read, log = {})
        val text = "Hello there. Turn it down."
        val said = out.speak(text)

        val phonemes = phonemizer.phonemize(text)
        assertEquals(2, phonemes.size)
        assertEquals(phonemes, out.lastPhonemes)
        assertEquals(phonemes.map { PhonemeIds.ids(it, phonemizer.idMap).toList() }, voice.calls.map { it.toList() })
        // sentence, 0.2 s of silence, sentence, then the tail that pushes it out
        assertEquals(listOf(500, 200, 500, 7), sink.writes.map { it.size })
        assertTrue(sink.writes[1].all { it == 0.toShort() })
        assertEquals((0.5f * 32767).toInt().toShort(), sink.writes[0][0])
        assertEquals(1, sink.starts)
        assertTrue(sink.stops >= 1)
        assertEquals(1.2, said.audioSeconds, 1e-9)
        assertNotNull(said.firstAudioNs)
        assertEquals(text, said.text)
    }

    @Test fun stopCutsOffTheSentenceBeingSynthesized() = runBlocking {
        val voice = FakeVoiceModel(gate = CountDownLatch(1))
        val sink = FakeSink()
        val out = PiperSpeechOut(phonemizer, voice, "en_US-norman-medium", sink, clock = clock.read, log = {})
        val said = async(Dispatchers.Default) { out.speak("This is a long sentence. And another one.") }
        assertTrue(voice.started.await(5, TimeUnit.SECONDS))
        out.stop()
        val result = withTimeout(5_000) { said.await() }
        assertNull(result.firstAudioNs)
        assertEquals(0.0, result.audioSeconds, 0.0)
        assertTrue(sink.writes.isEmpty())
        assertTrue(voice.cancels >= 1)
        assertEquals(1, voice.calls.size)
    }

    @Test fun synthesizeHandsBackTheAudioWithTheGapNotPlayed() = runBlocking {
        val sink = FakeSink()
        val out = PiperSpeechOut(phonemizer, FakeVoiceModel(), "en_US-kristin-medium", sink, clock = clock.read, log = {})
        val audio = out.synthesize("One. Two.")
        assertEquals(1000, audio.sampleRate)
        assertEquals(500 + 200 + 500, audio.samples.size)
        assertEquals(0f, audio.samples[600])
        assertTrue(sink.writes.isEmpty())
    }

    @Test fun itNamesTheVoiceForTheResults() {
        val out = PiperSpeechOut(phonemizer, FakeVoiceModel(sampleRate = 22050), "en_US-norman-medium", FakeSink(), threads = 4)
        assertEquals("en_US-norman-medium", out.voiceKey)
        assertTrue(out.describe(), out.describe().startsWith("$DOMOVOI_VOICE en_US-norman-medium (Piper VITS, 22050 Hz)"))
        assertTrue(out.describe().contains("4 threads"))
    }

    @Test fun pipersScalingAndPcm() {
        assertArrayEquals(floatArrayOf(0.5f, -1f), PiperVoice.peakNormalize(floatArrayOf(0.25f, -0.5f)), 1e-6f)
        // A near-silent sentence is lifted at most 100x.
        assertArrayEquals(floatArrayOf(0.1f), PiperVoice.peakNormalize(floatArrayOf(0.001f)), 1e-6f)
        assertArrayEquals(shortArrayOf(32767, -32767, 0), PiperSpeechOut.toPcm16(floatArrayOf(1f, -1.5f, 0f)))
    }

    @Test fun theConfigGivesTheRateTheScalesAndTheIdMap() {
        val ids = RepoPhonemizer.data.idMap
        val json = buildJsonObject {
            putJsonObject("audio") { put("sample_rate", 22050); put("quality", "medium") }
            putJsonObject("inference") { put("noise_scale", 0.667); put("length_scale", 1); put("noise_w", 0.333) }
            putJsonObject("phoneme_id_map") {
                ids.forEach { (k, v) -> put(k, JsonArray(v.map { JsonPrimitive(it) })) }
            }
        }.toString()
        val config = PiperConfig.parse(json)
        assertEquals(22050, config.sampleRate)
        assertEquals(0.667f, config.noiseScale, 1e-6f)
        assertEquals(1f, config.lengthScale, 1e-6f)
        assertEquals(0.333f, config.noiseW, 1e-6f)
        assertEquals(ids.keys, config.idMap.keys)
        assertEquals(ids.mapValues { it.value.toList() }, config.idMap.mapValues { it.value.toList() })
    }

    @Test fun theReportSaysWhichVoiceSpoke() {
        val device = DeviceFacts("Acme", "Phone 9", null, "16", 36, 8_000_000_000, false, "arm64-v8a", "1.0.0")
        val piper = renderBenchReport(device, BenchResults(voice = "$DOMOVOI_VOICE en_US-norman-medium (Piper VITS, 22050 Hz)"), "now")
        assertTrue(piper, piper.contains("speaking: the Domovoi voice (Piper, on ONNX Runtime 1.28.0)"))
        assertTrue(piper.contains("voice: $DOMOVOI_VOICE en_US-norman-medium"))
        val android = renderBenchReport(device, BenchResults(voice = "Android TextToSpeech, engine X"), "now")
        assertTrue(android.contains("speaking: Android TextToSpeech"))
    }

    /** Norman, then Kristin, read the lines: each block names its voice. */
    @Test fun samplesFromTwoVoicesAreReportedApart() {
        val device = DeviceFacts("Acme", "Phone 9", null, "16", 36, 8_000_000_000, false, "arm64-v8a", "1.0.0")
        val line = VoiceSamples.LINES[0]
        val results = BenchResults(samples = listOf(
            VoiceSampleResult(line, 300, 3000, 2.8, "en_US-norman-medium"),
            VoiceSampleResult(line, 200, 2900, 2.6, "en_US-kristin-medium"),
            VoiceSampleResult(VoiceSamples.LINES[1], 400, 7000, 6.0, "en_US-kristin-medium"),
        ))
        val text = renderBenchReport(device, results, "now")
        val norman = text.indexOf("  en_US-norman-medium:")
        val kristin = text.indexOf("  en_US-kristin-medium:")
        assertTrue(text, norman in 0 until kristin)
        val normanBlock = text.substring(norman, kristin)
        val kristinBlock = text.substring(kristin)
        assertTrue(normanBlock, normanBlock.contains("median to the first word: 300 ms\n  1. first word 300 ms"))
        assertTrue(kristinBlock, kristinBlock.contains("median to the first word: 300 ms\n  1. first word 200 ms"))
        assertTrue(kristinBlock.contains("  2. first word 400 ms"))
    }

    @Test fun aSampleRecordsWhichVoiceReadIt() = runBlocking {
        val out = PiperSpeechOut(phonemizer, FakeVoiceModel(), "en_US-ljspeech-medium", FakeSink(), clock = clock.read, log = {})
        val results = runVoiceSamples(out, listOf("Hi."), gapMs = 0)
        assertEquals("en_US-ljspeech-medium", results.single().voice)
        assertEquals(null, runVoiceSamples(FakeSpeechOut(clock), listOf("Hi."), gapMs = 0).single().voice)
    }

    @Test fun theConfigNamesThePhonemeSet() {
        fun cfg(espeak: String?, type: String? = null) = PiperConfig.parse(buildJsonObject {
            putJsonObject("audio") { put("sample_rate", 22050) }
            if (espeak != null) putJsonObject("espeak") { put("voice", espeak) }
            if (type != null) put("phoneme_type", type)
            putJsonObject("phoneme_id_map") { put("_", JsonArray(listOf(JsonPrimitive(0)))) }
        }.toString())
        assertEquals("en", cfg("en").dialect)
        assertEquals("en-us", cfg("en-us").dialect)       // the owner's uploads, most voices people train
        assertEquals("en-us", cfg("en_US").dialect)
        assertNull(cfg("de").dialect)
        assertNull(cfg("en-us", type = "text").dialect)
        assertNull(cfg(null).dialect)
    }

    @Test fun anEnUsVoiceIsSpokenInAmericanPhonemes() = runBlocking {
        val voice = FakeVoiceModel()
        val out = PiperSpeechOut(phonemizer, voice, "kronk", FakeSink(), clock = clock.read, log = {}, dialect = "en-us")
        out.speak("Better water.")
        assertEquals(listOf("bˈɛɾɚ wˈɔːɾɚ."), out.lastPhonemes)
        assertEquals(listOf(PhonemeIds.ids("bˈɛɾɚ wˈɔːɾɚ.", phonemizer.idMap).toList()), voice.calls.map { it.toList() })
    }

    @Test fun aSymbolTheVoiceLacksIsSaidAsTheNearestOne() = runBlocking {
        val small = phonemizer.idMap.filterKeys { it != "ɾ" && it != "ᵻ" }
        val voice = FakeVoiceModel()
        val logged = mutableListOf<String>()
        val out = PiperSpeechOut(phonemizer, voice, "small", FakeSink(), idMap = small, clock = clock.read,
            log = { logged += it }, dialect = "en-us")
        out.speak("Better, remind me.")
        assertEquals(listOf(PhonemeIds.ids("bˈɛtɚ, ɹɪmˈaɪnd mˌiː.", small).toList()), voice.calls.map { it.toList() })
        assertEquals(1, logged.count { "has no" in it })
        out.speak("Better.")
        assertEquals(1, logged.count { "has no" in it })     // once per voice
    }

    @Test fun aSentenceWithNoSoundTheVoiceKnowsIsNotSent() = runBlocking {
        val tiny = phonemizer.idMap.filterKeys { it in setOf("^", "$", "_", " ", ".") }
        val voice = FakeVoiceModel()
        val out = PiperSpeechOut(phonemizer, voice, "tiny", FakeSink(), idMap = tiny, clock = clock.read, log = {})
        val said = out.speak("Hello there.")
        assertTrue(voice.calls.isEmpty())
        assertEquals(0.0, said.audioSeconds, 0.0)
    }
}
