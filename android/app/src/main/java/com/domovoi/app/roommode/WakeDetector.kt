package com.domovoi.app.roommode

import java.util.Random

/**
 * openWakeWord's shared audio front end: the melspectrogram model and
 * Google's speech-embedding model. Every wake word in a room runs on its
 * output ([WakeDetector]); [OrtWakeFrontEnd] runs it on ONNX Runtime, the
 * tests use fakes.
 */
interface WakeFrontEnd : AutoCloseable {
    /** Raw melspectrogram of [samples] (int16 values as floats): frames x 32. */
    fun melspectrogram(samples: FloatArray): Array<FloatArray>

    /** One 96-value embedding of a 76 x 32 window of transformed mel frames. */
    fun embedding(window: Array<FloatArray>): FloatArray

    override fun close() {}
}

/**
 * One wake word's model: a small classifier over the front end's
 * embeddings ([OrtWakeClassifier]; ~1 MB, a few hundredths of a
 * millisecond a chunk).
 */
interface WakeClassifier : AutoCloseable {
    /** How many embeddings it reads (16 for the stock models). */
    val inputFrames: Int

    /** The word's score, 0..1, for the last [inputFrames] embeddings. */
    fun score(features: Array<FloatArray>): Float

    override fun close() {}
}

/**
 * openWakeWord's streaming detector (its `AudioFeatures` and
 * `Model.predict`, openWakeWord 0.6.0, Apache-2.0), rebuilt in Kotlin so a
 * phone hears the same wake words a Pi hears with the same models:
 *
 * - audio goes in as 80 ms chunks (1280 samples at 16 kHz);
 * - each chunk's melspectrogram is taken over the chunk and the 480
 *   samples before it, transformed `x / 10 + 2` (what makes the ONNX model
 *   match Google's), and kept (at most 970 frames, ~10 s);
 * - one embedding per chunk, of the last 76 mel frames, kept (at most 120);
 * - each word's classifier scores the last 16 embeddings;
 * - the first five scores after a reset are 0, as openWakeWord's are.
 *
 * **Several words, one front end** (1.4.0, [WakeWords]): the melspectrogram
 * and the embedding are computed ONCE per chunk, whatever the number of
 * [classifiers]; each word adds only its classifier's run over the same
 * embedding history, as openWakeWord's `Model` does with several models.
 *
 * After a reset the embedding history is seeded with the embeddings of four
 * seconds of low noise, as openWakeWord seeds it; computed once per load
 * ([noiseFeatures]) and reused on every reset, as the Pi does
 * (`_prime_wake_reset`), so a reset costs nothing.
 *
 * Not thread-safe: one listening thread feeds it.
 */
class WakeDetector(
    private val frontEnd: WakeFrontEnd,
    /** One per word, main word first: [accept]'s scores are in this order. */
    private val classifiers: List<WakeClassifier>,
    /** The seed history after a reset; [noiseFeatures] by default. */
    seed: List<FloatArray>? = null,
) : AutoCloseable {
    init {
        require(classifiers.isNotEmpty()) { "a wake detector needs at least one word" }
    }

    /** How many words it scores. */
    val words: Int get() = classifiers.size

    private val noise: List<FloatArray> = seed ?: noiseFeatures(frontEnd)
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
     * frames). Returns each word's highest score of the chunks this
     * completed (one per classifier, in order), or null when no 80 ms
     * chunk completed.
     */
    fun accept(samples: ShortArray, count: Int = samples.size): FloatArray? {
        var best: FloatArray? = null
        var at = 0
        while (at < count) {
            val take = minOf(CHUNK - pendingFill, count - at)
            System.arraycopy(samples, at, pending, pendingFill, take)
            pendingFill += take
            at += take
            if (pendingFill == CHUNK) {
                val scores = chunk(pending)
                pendingFill = 0
                val b = best
                if (b == null) best = scores else for (i in b.indices) b[i] = maxOf(b[i], scores[i])
            }
        }
        return best
    }

    /** One 80 ms chunk: every word's score. */
    private fun chunk(samples: ShortArray): FloatArray {
        // The raw history: the 480 samples before this chunk, then the chunk.
        val keep = minOf(rawFill, CONTEXT)
        System.arraycopy(raw, rawFill - keep, raw, 0, keep)
        System.arraycopy(samples, 0, raw, keep, CHUNK)
        rawFill = keep + CHUNK
        val input = FloatArray(rawFill) { raw[it].toFloat() }
        // The shared front end: once per chunk, for every word.
        for (frame in frontEnd.melspectrogram(input)) {
            mel.addLast(FloatArray(MEL_BINS) { frame[it] / 10f + 2f })
        }
        while (mel.size > MEL_MAX) mel.removeFirst()
        if (mel.size >= WINDOW) {
            val window = Array(WINDOW) { mel[mel.size - WINDOW + it] }
            features.addLast(frontEnd.embedding(window))
            while (features.size > FEATURES_MAX) features.removeFirst()
        }
        predictions += 1
        val silent = predictions <= WARMUP
        return FloatArray(classifiers.size) { i ->
            val c = classifiers[i]
            val n = c.inputFrames
            val score = if (features.size >= n) c.score(Array(n) { features[features.size - n + it] }) else 0f
            if (silent) 0f else score
        }
    }

    override fun close() {
        classifiers.forEach { runCatching { it.close() } }
        runCatching { frontEnd.close() }
    }

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
        fun noiseFeatures(nets: WakeFrontEnd, noise: ShortArray? = null): List<FloatArray> {
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
