package com.domovoi.app.roommode

import java.util.Random

/**
 * The three networks of openWakeWord's pipeline, each run on its own: the
 * melspectrogram model, Google's speech-embedding model and a wake-word
 * model. [OrtWakeNets] runs them on ONNX Runtime; the tests use fakes.
 */
interface WakeNets : AutoCloseable {
    /** Raw melspectrogram of [samples] (int16 values as floats): frames x 32. */
    fun melspectrogram(samples: FloatArray): Array<FloatArray>

    /** One 96-value embedding of a 76 x 32 window of transformed mel frames. */
    fun embedding(window: Array<FloatArray>): FloatArray

    /** How many embeddings the wake model reads (16 for the stock models). */
    val wakeInputFrames: Int

    /** The wake word's score, 0..1, for the last [wakeInputFrames] embeddings. */
    fun wake(features: Array<FloatArray>): Float

    override fun close() {}
}

/**
 * openWakeWord's streaming detector (its `AudioFeatures` and
 * `Model.predict`, openWakeWord 0.6.0, Apache-2.0), rebuilt in Kotlin so a
 * phone hears the same wake word a Pi hears with the same models:
 *
 * - audio goes in as 80 ms chunks (1280 samples at 16 kHz);
 * - each chunk's melspectrogram is taken over the chunk and the 480
 *   samples before it, transformed `x / 10 + 2` (what makes the ONNX model
 *   match Google's), and kept (at most 970 frames, ~10 s);
 * - one embedding per chunk, of the last 76 mel frames, kept (at most 120);
 * - the wake model scores the last 16 embeddings;
 * - the first five scores after a reset are 0, as openWakeWord's are.
 *
 * After a reset the embedding history is seeded with the embeddings of four
 * seconds of low noise, as openWakeWord seeds it; computed once per model
 * ([noiseFeatures]) and reused on every reset, as the Pi does
 * (`_prime_wake_reset`), so a reset costs nothing.
 *
 * Not thread-safe: one listening thread feeds it.
 */
class WakeDetector(
    private val nets: WakeNets,
    /** The seed history after a reset; [noiseFeatures] by default. */
    seed: List<FloatArray>? = null,
) : AutoCloseable {
    private val noise: List<FloatArray> = seed ?: noiseFeatures(nets)
    private val raw = ShortArray(CHUNK + CONTEXT)
    private var rawFill = 0
    private val mel = ArrayDeque<FloatArray>()
    private val features = ArrayDeque<FloatArray>()
    private val pending = ShortArray(CHUNK)
    private var pendingFill = 0
    private var predictions = 0

    init {
        reset()
    }

    fun reset() {
        rawFill = 0
        pendingFill = 0
        mel.clear()
        repeat(WINDOW) { mel.addLast(FloatArray(MEL_BINS) { 1f }) }
        features.clear()
        noise.forEach { features.addLast(it) }
        predictions = 0
    }

    /**
     * Feed [count] samples of [samples] (any length; a Pi feeds 30 ms
     * frames). Returns the highest score of the chunks this completed, or
     * null when no 80 ms chunk completed.
     */
    fun accept(samples: ShortArray, count: Int = samples.size): Float? {
        var best: Float? = null
        var at = 0
        while (at < count) {
            val take = minOf(CHUNK - pendingFill, count - at)
            System.arraycopy(samples, at, pending, pendingFill, take)
            pendingFill += take
            at += take
            if (pendingFill == CHUNK) {
                val score = chunk(pending)
                pendingFill = 0
                best = if (best == null) score else maxOf(best, score)
            }
        }
        return best
    }

    /** One 80 ms chunk: its score. */
    private fun chunk(samples: ShortArray): Float {
        // The raw history: the 480 samples before this chunk, then the chunk.
        val keep = minOf(rawFill, CONTEXT)
        System.arraycopy(raw, rawFill - keep, raw, 0, keep)
        System.arraycopy(samples, 0, raw, keep, CHUNK)
        rawFill = keep + CHUNK
        val input = FloatArray(rawFill) { raw[it].toFloat() }
        for (frame in nets.melspectrogram(input)) {
            mel.addLast(FloatArray(MEL_BINS) { frame[it] / 10f + 2f })
        }
        while (mel.size > MEL_MAX) mel.removeFirst()
        if (mel.size >= WINDOW) {
            val window = Array(WINDOW) { mel[mel.size - WINDOW + it] }
            features.addLast(nets.embedding(window))
            while (features.size > FEATURES_MAX) features.removeFirst()
        }
        val n = nets.wakeInputFrames
        val score = if (features.size >= n) {
            nets.wake(Array(n) { features[features.size - n + it] })
        } else {
            0f
        }
        predictions += 1
        return if (predictions <= WARMUP) 0f else score
    }

    override fun close() = nets.close()

    companion object {
        const val CHUNK = 1280
        const val CONTEXT = 160 * 3
        const val MEL_BINS = 32
        const val WINDOW = 76
        const val STEP = 8
        const val MEL_MAX = 10 * 97
        const val FEATURES_MAX = 120
        const val WARMUP = 5
        const val NOISE_SECONDS = 4

        /** openWakeWord's `_get_embeddings` over 4 s of noise in
         *  [-1000, 1000): every 8th 76-frame window's embedding. [noise]
         *  null: seeded noise (fixed seed, so every load is alike). */
        fun noiseFeatures(nets: WakeNets, noise: ShortArray? = null): List<FloatArray> {
            val audio = noise ?: Random(0x0D0E).let { r ->
                ShortArray(16_000 * NOISE_SECONDS) { (r.nextInt(2000) - 1000).toShort() }
            }
            val spec = nets.melspectrogram(FloatArray(audio.size) { audio[it].toFloat() })
                .map { f -> FloatArray(MEL_BINS) { f[it] / 10f + 2f } }
            val out = ArrayList<FloatArray>()
            var i = 0
            while (i + WINDOW <= spec.size) {
                out += nets.embedding(Array(WINDOW) { spec[i + it] })
                i += STEP
            }
            return out
        }
    }
}
