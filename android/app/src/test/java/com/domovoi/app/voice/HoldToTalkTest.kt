package com.domovoi.app.voice

import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * The chat mic's capture: press and hold records, release keeps what was
 * said, sliding off throws it away, 30 s is the most one turn takes, and
 * the mic is closed whatever happens.
 */
class HoldToTalkTest {

    /** A mic that hands out the same quiet tone for ever, a chunk at a time. */
    private class FakeMic(private val value: Short = 1000, private val failAfter: Int = Int.MAX_VALUE) : AudioSource {
        var started = false
        var stopped = false
        var reads = 0

        override fun start() {
            started = true
        }

        override fun read(buffer: ShortArray): Int {
            if (++reads > failAfter) return -3
            Thread.sleep(2)
            buffer.fill(value)
            return buffer.size
        }

        override fun stop() {
            stopped = true
        }
    }

    @Test fun releaseKeepsWhatWasSaidAsLittleEndianPcm() = runBlocking {
        val mic = FakeMic(value = 0x0102)
        val rec = HoldToTalkRecorder({ mic })
        val job = async { rec.record() }
        delay(60)
        rec.release()
        val got = job.await()
        assertNotNull(got)
        got!!
        assertEquals(HeldEnd.RELEASE, got.endedBy)
        assertTrue(got.pcm.isNotEmpty() && got.pcm.size % (2 * HoldToTalkRecorder.CHUNK_SAMPLES) == 0)
        // Little-endian 0x0102: 02 01.
        assertEquals(0x02.toByte(), got.pcm[0])
        assertEquals(0x01.toByte(), got.pcm[1])
        assertTrue(mic.started && mic.stopped)
    }

    @Test fun slidingOffThrowsTheRecordingAway() = runBlocking {
        val mic = FakeMic()
        val rec = HoldToTalkRecorder({ mic })
        val job = async { rec.record() }
        delay(40)
        rec.cancel()
        assertNull(job.await())
        assertTrue(mic.stopped)
    }

    @Test fun aCancelBeforeTheMicOpensNeverOpensIt() = runBlocking {
        val mic = FakeMic()
        val rec = HoldToTalkRecorder({ mic })
        rec.cancel()
        assertNull(rec.record())
        assertTrue(!mic.started)
    }

    @Test fun aReleaseBeforeTheMicOpensIsAnEmptyRecordingNotAnEndlessOne() = runBlocking {
        val mic = FakeMic()
        val rec = HoldToTalkRecorder({ mic })
        rec.release()
        val got = rec.record()!!
        assertEquals(0, got.pcm.size)
        assertTrue(got.seconds < HoldToTalkRecorder.MIN_SECONDS)
        assertTrue(mic.stopped)
    }

    @Test fun theLongestTurnEndsTheRecordingByItself() = runBlocking {
        val rec = HoldToTalkRecorder({ FakeMic() }, maxSeconds = 0.1)
        val got = rec.record()!!
        assertEquals(HeldEnd.LENGTH_CAP, got.endedBy)
        // Exactly the cap, not the chunk that crossed it.
        assertEquals((0.1 * VOICE_SAMPLE_RATE).toInt() * 2, got.pcm.size)
        assertEquals(0.1, got.seconds, 0.001)
    }

    @Test fun aMicThatStopsDeliveringIsAnErrorAndStillClosed() = runBlocking {
        val mic = FakeMic(failAfter = 2)
        val rec = HoldToTalkRecorder({ mic })
        try {
            rec.record()
            throw AssertionError("expected the mic failure to surface")
        } catch (e: IOException) {
            assertTrue(e.message!!.contains("stopped"))
        }
        assertTrue(mic.stopped)
    }

    @Test fun theLevelMeterIsZeroForSilenceAndGrowsWithLoudness() {
        val quiet = ShortArray(512)
        val loud = ShortArray(512) { if (it % 2 == 0) 8000 else -8000 }
        assertEquals(0f, level(quiet, quiet.size))
        assertTrue(level(loud, loud.size) > 0.5f)
        assertEquals(0f, level(loud, 0))
    }

    @Test fun samplesAreWrittenLittleEndian() {
        val out = ByteArrayOutputStream()
        appendLittleEndian(out, shortArrayOf(1, -2, 0x7FFF), 2)
        val bytes = out.toByteArray()
        assertEquals(4, bytes.size)
        val b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        assertEquals(1.toShort(), b.getShort(0))
        assertEquals((-2).toShort(), b.getShort(2))
    }

    @Test fun aWavIsReadWhateverChunksComeBeforeItsData() {
        val pcm = ByteArray(800) { (it % 7).toByte() }
        val plain = wav(pcm, rate = 22_050)
        val parsed = parseWav(plain)!!
        assertEquals(22_050, parsed.sampleRate)
        assertEquals(1, parsed.channels)
        assertArrayEquals(pcm, parsed.data)

        // A LIST chunk between fmt and data (and an odd-sized one, padded).
        val withList = wav(pcm, rate = 16_000, extra = "LIST" to ByteArray(5) { 'x'.code.toByte() })
        assertArrayEquals(pcm, parseWav(withList)!!.data)

        // A streamed writer's placeholder size: read to the end of the file.
        val placeholder = plain.copyOf().also { ByteBuffer.wrap(it).order(ByteOrder.LITTLE_ENDIAN).putInt(40, Int.MAX_VALUE) }
        assertArrayEquals(pcm, parseWav(placeholder)!!.data)

        // The stub TTS's empty WAV: no audio, still a WAV.
        assertEquals(0, parseWav(wav(ByteArray(0), rate = 16_000))!!.data.size)
    }

    @Test fun anythingButPcm16IsNotPlayed() {
        assertNull(parseWav(ByteArray(10)))
        assertNull(parseWav("RIFF\u0000\u0000\u0000\u0000AVI LIST".toByteArray()))
        assertNull(parseWav(wav(ByteArray(8), rate = 16_000, bits = 8)))
        assertNull(parseWav(wav(ByteArray(8), rate = 16_000, format = 3)))
    }

    private fun wav(
        pcm: ByteArray,
        rate: Int,
        channels: Int = 1,
        bits: Int = 16,
        format: Int = 1,
        extra: Pair<String, ByteArray>? = null,
    ): ByteArray {
        val extraBytes = extra?.let { 8 + it.second.size + (it.second.size and 1) } ?: 0
        val b = ByteBuffer.allocate(44 + extraBytes + pcm.size).order(ByteOrder.LITTLE_ENDIAN)
        b.put("RIFF".toByteArray()).putInt(36 + extraBytes + pcm.size).put("WAVE".toByteArray())
        b.put("fmt ".toByteArray()).putInt(16).putShort(format.toShort()).putShort(channels.toShort())
            .putInt(rate).putInt(rate * channels * bits / 8).putShort((channels * bits / 8).toShort()).putShort(bits.toShort())
        if (extra != null) {
            b.put(extra.first.toByteArray()).putInt(extra.second.size).put(extra.second)
            if (extra.second.size and 1 == 1) b.put(0)
        }
        b.put("data".toByteArray()).putInt(pcm.size).put(pcm)
        return b.array()
    }
}
