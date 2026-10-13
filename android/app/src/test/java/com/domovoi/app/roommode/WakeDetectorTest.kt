package com.domovoi.app.roommode

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * openWakeWord's streaming detector in Kotlin. With fake networks: the
 * buffering (80 ms chunks, the 480-sample context, 76-frame windows, the
 * 16-embedding input, the five silent warm-up scores). With the real
 * models on ONNX Runtime for the JVM: the same scores openWakeWord 0.6.0
 * gives a recorded "hey jarvis" (functional-testing/room-mode/
 * oww_reference.py) — run when -Pdomovoi.oww.dir and -Pdomovoi.room.clips
 * point at the models and the clips (not in the repo: their licence).
 */
class WakeDetectorTest {
    /** Records what the pipeline asks of each network. */
    private class FakeNets(private val scores: (Int) -> Float = { 0.9f }) : WakeNets {
        val melInputs = mutableListOf<Int>()
        var embeddings = 0
        var wakes = 0
        val windows = mutableListOf<Float>()
        override val wakeInputFrames = 16

        override fun melspectrogram(samples: FloatArray): Array<FloatArray> {
            melInputs += samples.size
            val frames = Math.ceil(samples.size / 160.0 - 3).toInt()
            return Array(frames) { FloatArray(32) { samples.last() } }
        }

        override fun embedding(window: Array<FloatArray>): FloatArray {
            assertEquals(76, window.size)
            embeddings += 1
            windows += window.last()[0]
            return FloatArray(96) { embeddings.toFloat() }
        }

        override fun wake(features: Array<FloatArray>): Float {
            assertEquals(16, features.size)
            wakes += 1
            return scores(wakes)
        }
    }

    @Test
    fun eightyMillisecondChunksWithTheirContext() {
        val nets = FakeNets()
        val d = WakeDetector(nets, seed = List(41) { FloatArray(96) })
        // 30 ms frames, as the room's mic delivers them: 1280 samples is
        // 2.67 frames, so a chunk completes on the 3rd, 6th and 8th frame.
        val frame = ShortArray(480) { 100 }
        val results = (1..8).map { d.accept(frame) }
        assertEquals(listOf(false, false, true, false, false, true, false, true), results.map { it != null })
        // First chunk after a reset has no context; the next ones carry 480.
        assertEquals(listOf(1280, 1760, 1760), nets.melInputs)
        // One embedding and one score per chunk.
        assertEquals(3, nets.embeddings)
        assertEquals(3, nets.wakes)
    }

    @Test
    fun theFirstFiveScoresAfterAResetAreSilent() {
        val nets = FakeNets { 0.99f }
        val d = WakeDetector(nets, seed = List(41) { FloatArray(96) })
        val chunk = ShortArray(1280)
        val scores = (1..7).map { d.accept(chunk)!! }
        assertEquals(listOf(0f, 0f, 0f, 0f, 0f, 0.99f, 0.99f), scores)
        d.reset()
        assertEquals(0f, d.accept(chunk))
    }

    @Test
    fun theMelTransformIsAppliedBeforeTheEmbedding() {
        val nets = FakeNets()
        val d = WakeDetector(nets, seed = emptyList())
        d.accept(ShortArray(1280) { 30 })
        // mel value 30 -> 30 / 10 + 2 = 5 in the newest frame of the window
        assertEquals(5f, nets.windows.last(), 1e-6f)
        assertNull(d.accept(ShortArray(100)))
    }

    @Test
    fun theNoiseSeedIsEveryEighthWindowOfFourSeconds() {
        val nets = FakeNets()
        val seed = WakeDetector.noiseFeatures(nets)
        // 64000 samples -> 397 mel frames -> windows at 0, 8, ..., 320.
        assertEquals(41, seed.size)
        assertEquals(listOf(64000), nets.melInputs)
    }

    // ── the real models ─────────────────────────────────────────────────

    private fun realModels(): File? = System.getProperty("domovoi.oww.dir")?.let(::File)?.takeIf {
        File(it, "melspectrogram.onnx").isFile && File(it, "hey_jarvis_v0.1.onnx").isFile
    }

    private fun clips(): File? = System.getProperty("domovoi.room.clips")?.let(::File)?.takeIf { it.isDirectory }

    @Test
    fun theSameScoresOpenWakeWordGives() {
        val models = realModels()
        val clips = clips()
        assumeTrue("needs -Pdomovoi.oww.dir and -Pdomovoi.room.clips", models != null && clips != null)
        for (name in listOf("hey_jarvis", "what_time")) {
            val ref = File(clips, "$name.oww.json")
            assumeTrue("missing $ref", ref.isFile)
            val want = Json.parseToJsonElement(ref.readText()).jsonObject["scores"]!!.jsonArray.map { it.jsonPrimitive.content.toFloat() }
            val pcm = readWav(File(clips, "$name.wav"))
            val pad = ShortArray(16_000)
            val audio = pad + pcm + pad
            val nets = OrtWakeNets(File(models, "melspectrogram.onnx"), File(models, "embedding_model.onnx"), File(models, "hey_jarvis_v0.1.onnx"))
            nets.use {
                val d = WakeDetector(nets, seed = WakeDetector.noiseFeatures(nets, ShortArray(64_000)))
                val got = (0..audio.size - 1280 step 1280).map { d.accept(audio.copyOfRange(it, it + 1280))!! }
                assertEquals(want.size, got.size)
                for (i in got.indices) assertEquals("$name chunk $i", want[i], got[i], 0.02f)
                if (name == "hey_jarvis") assertTrue("it fires", got.max() > 0.5f) else assertTrue("it stays quiet", got.max() < 0.1f)
            }
        }
    }

    @Test
    fun sileroHearsSpeechAndNotSilence() {
        val models = realModels()
        val clips = clips()
        assumeTrue("needs -Pdomovoi.oww.dir and -Pdomovoi.room.clips", models != null && clips != null && File(models, "silero_vad.onnx").isFile)
        SileroVad(File(models, "silero_vad.onnx")).use { vad ->
            val silence = (0 until 20).map { vad.probability(ShortArray(480)) }
            assertTrue(silence.toString(), silence.max() < 0.2f)
            vad.reset()
            val pcm = readWav(File(clips!!, "what_time.wav"))
            val probs = (0..pcm.size - 480 step 480).map { vad.probability(pcm.copyOfRange(it, it + 480)) }
            assertTrue(probs.toString(), probs.count { it > 0.5f } > probs.size / 4)
        }
    }

    companion object {
        /** 16 kHz mono PCM16 samples of a canonical WAV file. */
        fun readWav(file: File): ShortArray {
            val b = file.readBytes()
            var at = 12
            while (at + 8 <= b.size) {
                val id = String(b, at, 4, Charsets.US_ASCII)
                val size = (b[at + 4].toInt() and 0xff) or ((b[at + 5].toInt() and 0xff) shl 8) or
                    ((b[at + 6].toInt() and 0xff) shl 16) or ((b[at + 7].toInt() and 0xff) shl 24)
                if (id == "data") {
                    val n = minOf(size, b.size - at - 8) / 2
                    return ShortArray(n) { ((b[at + 8 + 2 * it].toInt() and 0xff) or (b[at + 9 + 2 * it].toInt() shl 8)).toShort() }
                }
                at += 8 + size + (size and 1)
            }
            error("no data chunk in $file")
        }
    }
}
