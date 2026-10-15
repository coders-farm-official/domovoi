package com.domovoi.app.assistant

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** The pure parts of speaking with Android's TextToSpeech: which voice, how
 *  it is described, and reading the audio file the self-test gets back. */
class AndroidSpeechOutTest {

    private fun v(
        name: String,
        lang: String = "en",
        country: String = "US",
        quality: Int = 300,
        latency: Int = 300,
        network: Boolean = false,
        notInstalled: Boolean = false,
    ) = VoiceChoice(name, lang, country, quality, latency, network, notInstalled)

    // ── Choosing the voice ──────────────────────────────────────────────

    @Test fun aVoiceThatNeedsTheNetworkIsNeverChosen() {
        val voices = listOf(v("cloud", quality = 500, network = true), v("local", quality = 200))
        assertEquals("local", pickVoice(voices, "en", "US")?.name)
        assertNull(pickVoice(listOf(v("cloud", network = true)), "en", "US"))
    }

    @Test fun aVoiceNotYetDownloadedIsSkipped() {
        val voices = listOf(v("missing", quality = 500, notInstalled = true), v("here"))
        assertEquals("here", pickVoice(voices, "en", "US")?.name)
    }

    @Test fun thePhonesCountryFirstThenQualityThenSpeed() {
        val voices = listOf(
            v("gb-best", country = "GB", quality = 500),
            v("us-normal", quality = 300, latency = 200),
            v("us-high-slow", quality = 400, latency = 400),
            v("us-high-quick", quality = 400, latency = 200),
        )
        assertEquals("us-high-quick", pickVoice(voices, "en", "US")?.name)
        assertEquals("gb-best", pickVoice(voices, "en", "GB")?.name)
        // No voice for the country: the best English one.
        assertEquals("gb-best", pickVoice(voices, "en", "AU")?.name)
    }

    @Test fun onlyTheAskedLanguageAndTiesBreakByName() {
        val voices = listOf(v("de", lang = "de", country = "DE", quality = 500), v("b"), v("a"))
        assertEquals("a", pickVoice(voices, "en", "US")?.name)
        assertNull(pickVoice(voices.take(1), "en", "US"))
        assertEquals("a", pickVoice(listOf(v("a", lang = "EN")), "en", "US")?.name)
    }

    @Test fun theDescriptionNamesEngineVoiceAndTiming() {
        val info = SpeechVoiceInfo("Speech Services", "com.example.tts", v("en-us-x-abc-local", quality = 400, latency = 200), null)
        val text = info.describe()
        listOf("Android TextToSpeech", "Speech Services (com.example.tts)", "en-us-x-abc-local", "en-US",
            "high quality", "low latency", "on the phone").forEach { assertTrue("missing $it in $text", text.contains(it)) }
        assertFalse(text.contains("first word"))
        assertTrue(info.copy(wordTiming = true).describe().contains("word callback"))
        assertTrue(info.copy(wordTiming = false).describe().contains("start of playback"))
        assertEquals("very high", voiceLevelName(500))
        assertEquals("level 250", voiceLevelName(250))
    }

    // ── Reading the synthesized file ────────────────────────────────────

    @Test fun a16BitWavRoundTrips() {
        val samples = floatArrayOf(0f, 0.5f, -0.5f, 0.25f, -1f)
        val parsed = parseWav(wavBytes(samples, 24_000))!!
        assertEquals(24_000, parsed.sampleRate)
        assertEquals(samples.size, parsed.samples.size)
        samples.indices.forEach { assertEquals(samples[it], parsed.samples[it], 1f / 16_000) }
        assertEquals(5.0 / 24_000, parsed.seconds, 1e-12)
    }

    @Test fun floatStereoIsAveragedToMono() {
        val wav = wav(format = 3, channels = 2, rate = 22_050, bits = 32) { buf ->
            buf.putFloat(0.2f).putFloat(0.4f)     // frame 1
            buf.putFloat(-1f).putFloat(0f)        // frame 2
        }
        val parsed = parseWav(wav)!!
        assertEquals(22_050, parsed.sampleRate)
        assertArrayEquals(floatArrayOf(0.3f, -0.5f), parsed.samples, 1e-6f)
    }

    @Test fun extensibleFormatAndExtraChunksAreRead() {
        val wav = wav(format = 0xFFFE, channels = 1, rate = 16_000, bits = 16, subFormat = 1, extraChunk = true) { buf ->
            buf.putShort(16_384).putShort(-16_384)
        }
        assertArrayEquals(floatArrayOf(0.5f, -0.5f), parseWav(wav)!!.samples, 1e-6f)
    }

    @Test fun anUnsetDataSizeReadsToTheEnd() {
        val bytes = wavBytes(floatArrayOf(0.5f, 0.5f, 0.5f), 16_000)
        ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN).putInt(40, 0)   // the data chunk's size
        assertEquals(3, parseWav(bytes)!!.samples.size)
    }

    @Test fun whatIsNotAWavIsNull() {
        assertNull(parseWav(ByteArray(0)))
        assertNull(parseWav("RIFF....AVI LIST".toByteArray()))
        assertNull(parseWav(wav(format = 2, channels = 1, rate = 16_000, bits = 4) { it.putShort(0) }))   // ADPCM
        val noFmt = wavBytes(floatArrayOf(0f), 16_000).copyOf()
        "junk".toByteArray().copyInto(noFmt, 12)
        assertNull(parseWav(noFmt))
    }

    /** A WAV with a hand-made fmt chunk; [body] writes the samples. */
    private fun wav(
        format: Int,
        channels: Int,
        rate: Int,
        bits: Int,
        subFormat: Int? = null,
        extraChunk: Boolean = false,
        body: (ByteBuffer) -> Unit,
    ): ByteArray {
        val data = ByteBuffer.allocate(256).order(ByteOrder.LITTLE_ENDIAN).also(body)
        val dataBytes = data.position()
        val fmtSize = if (subFormat != null) 40 else 16
        val out = ByteBuffer.allocate(12 + 8 + fmtSize + (if (extraChunk) 14 else 0) + 8 + dataBytes)
            .order(ByteOrder.LITTLE_ENDIAN)
        out.put("RIFF".toByteArray()).putInt(out.capacity() - 8).put("WAVE".toByteArray())
        out.put("fmt ".toByteArray()).putInt(fmtSize)
            .putShort(format.toShort()).putShort(channels.toShort()).putInt(rate)
            .putInt(rate * channels * bits / 8).putShort((channels * bits / 8).toShort()).putShort(bits.toShort())
        if (subFormat != null) {
            out.putShort(22).putShort(bits.toShort()).putInt(0x4)         // cbSize, valid bits, channel mask
            out.putShort(subFormat.toShort()).put(ByteArray(14))           // the sub-format GUID
        }
        if (extraChunk) out.put("LIST".toByteArray()).putInt(5).put(ByteArray(6))   // odd size, padded
        out.put("data".toByteArray()).putInt(dataBytes).put(data.array(), 0, dataBytes)
        return out.array()
    }
}
