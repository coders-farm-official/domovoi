package com.domovoi.app.assistant

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

class MicCaptureTest {
    private val clock = TestClock()
    private val quiet = ShortArray(MicCapture.CHUNK_SAMPLES)
    private val loud = ShortArray(MicCapture.CHUNK_SAMPLES) { 16_384 }   // 0.5

    @Test fun silenceAfterSpeechEndsTheUtteranceAndClosesTheMic() = runBlocking {
        val source = FakeAudioSource(listOf(quiet, loud, loud, quiet, quiet, quiet, loud))
        var started = 0
        val u = MicCapture({ source }, clock.read).listen(FakeSpeechDetector(silenceChunks = 3)) { started++ }
        assertNotNull(u)
        assertEquals(EndedBy.SILENCE, u!!.endedBy)
        assertEquals(1, started)
        assertEquals(5 * MicCapture.CHUNK_SAMPLES, u.samples.size)    // two loud chunks + three quiet
        assertEquals(0.5f, u.samples.first(), 0f)
        assertTrue(source.started && source.stopped)
    }

    @Test fun theLengthCapEndsAnUnfinishedUtterance() = runBlocking {
        val source = FakeAudioSource(List(100) { loud })
        val u = MicCapture({ source }, clock.read).listen(FakeSpeechDetector(), maxSeconds = 0.5)
        assertEquals(EndedBy.LENGTH_CAP, u!!.endedBy)
        assertTrue(source.stopped)
    }

    @Test fun noSpeechAtAllIsNoUtterance() = runBlocking {
        val source = FakeAudioSource(emptyList())
        assertNull(MicCapture({ source }, clock.read).listen(FakeSpeechDetector(), maxSeconds = 0.3))
        assertTrue(source.stopped)
    }

    @Test fun stopEndsListeningWithWhatWasHeard() = runBlocking {
        val mic = MicCapture({ FakeAudioSource(List(1_000) { loud }) }, clock.read)
        val detector = object : SpeechDetector by FakeSpeechDetector() {
            var chunks = 0
            override fun accept(samples: FloatArray): FloatArray? {
                if (++chunks == 4) mic.stop()
                return null
            }
            override val hearingSpeech: Boolean get() = true
            override fun flush(): FloatArray = FloatArray(chunks)
        }
        val u = mic.listen(detector)
        assertEquals(EndedBy.TAP, u!!.endedBy)
        assertEquals(4, u.samples.size)
    }

    @Test fun aFailingMicIsAnErrorAndStillClosed() {
        val broken = object : AudioSource {
            var stopped = false
            override fun start() {}
            override fun read(buffer: ShortArray) = -3
            override fun stop() { stopped = true }
        }
        val thrown = runCatching { runBlocking { MicCapture({ broken }, clock.read).listen(FakeSpeechDetector()) } }.exceptionOrNull()
        assertTrue(thrown is IOException)
        assertTrue(broken.stopped)
    }

    // ── Sample helpers ──────────────────────────────────────────────────

    @Test fun pcm16BecomesFloatsInRange() {
        assertArrayEquals(floatArrayOf(0f, 0.5f, -1f), pcm16ToFloat(shortArrayOf(0, 16_384, Short.MIN_VALUE, 99), 3), 1e-6f)
    }

    @Test fun resamplingKeepsDurationAndShape() {
        val ramp = FloatArray(22_050) { it / 22_050f }
        val out = resample(ramp, 22_050, 16_000)
        assertEquals(16_000, out.size)
        assertEquals(0f, out.first(), 1e-6f)
        assertEquals(0.5f, out[8_000], 1e-3f)
        assertTrue(out.toList().zipWithNext().all { (a, b) -> b >= a })
        assertArrayEquals(ramp, resample(ramp, 16_000, 16_000), 0f)
    }

    @Test fun wavBytesAreAValidPcm16Header() {
        val wav = wavBytes(floatArrayOf(0f, 1f, -1f, 2f))
        assertEquals(44 + 8, wav.size)
        assertEquals("RIFF", String(wav, 0, 4))
        assertEquals("WAVE", String(wav, 8, 4))
        assertEquals("data", String(wav, 36, 4))
        val buf = java.nio.ByteBuffer.wrap(wav).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        assertEquals(16_000, buf.getInt(24))
        assertEquals(1, buf.getShort(22).toInt())                     // mono
        assertEquals(8, buf.getInt(40))
        assertEquals(32_767, buf.getShort(46).toInt())
        assertEquals(32_767, buf.getShort(50).toInt())                // clipped
    }
}
