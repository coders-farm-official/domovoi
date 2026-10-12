package com.domovoi.app.assistant

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import java.io.File

/** A clock the test moves: every read returns the current value. */
class TestClock(var nowNs: Long = 1_000_000_000L) {
    fun advanceMs(ms: Long) {
        nowNs += ms * 1_000_000
    }

    val read: () -> Long = { nowNs }
}

/**
 * Replies with [deltas], advancing [clock] by [msPerDelta] before each one
 * (and [firstDelayMs] before the first), so timings are exact.
 */
class FakeLlmEngine(
    private val clock: TestClock,
    var deltas: List<String> = listOf("Hello", " there."),
    var firstDelayMs: Long = 100,
    var msPerDelta: Long = 10,
    var stats: LlmStats? = LlmStats(0.1, 20, 2, 200.0, 12.5),
    var failWith: Throwable? = null,
) : LlmEngine {
    val calls = mutableListOf<Pair<String, List<ChatTurn>>>()
    var cancels = 0
    var loadedWith: Pair<File, LlmBackend>? = null
    override var isLoaded = false

    override suspend fun load(model: File, backend: LlmBackend) {
        loadedWith = model to backend
        isLoaded = true
    }

    override fun reply(system: String, history: List<ChatTurn>): Flow<String> = flow {
        calls += system to history
        failWith?.let { throw it }
        deltas.forEachIndexed { i, d ->
            clock.advanceMs(if (i == 0) firstDelayMs else msPerDelta)
            emit(d)
        }
    }

    override fun cancel() {
        cancels++
    }

    override fun lastStats(): LlmStats? = stats

    override fun close() {
        isLoaded = false
    }
}

class FakeSpeechToText(private val clock: TestClock, var text: String = "what time is it", var ms: Long = 200) : SpeechToText {
    val heard = mutableListOf<FloatArray>()

    override fun transcribe(samples: FloatArray): String {
        heard += samples
        clock.advanceMs(ms)
        return text
    }

    override fun close() {}
}

/** Speaks instantly after [synthMs] of "synthesis"; records what it said. */
class FakeSpeechOut(private val clock: TestClock, var synthMs: Long = 150) : SpeechOut {
    val spoken = mutableListOf<String>()
    var stops = 0

    override suspend fun speak(sentence: String): SpokenSentence {
        val start = clock.nowNs
        clock.advanceMs(synthMs)
        val first = clock.nowNs
        spoken += sentence
        return SpokenSentence(sentence, start, first, clock.nowNs, 1.0)
    }

    override fun stop() {
        stops++
    }

    override fun close() {}
}

/**
 * A detector driven by amplitude: speech is any chunk with a sample above
 * [threshold]; the utterance ends after [silenceChunks] quiet chunks.
 */
class FakeSpeechDetector(private val threshold: Float = 0.1f, private val silenceChunks: Int = 3) : SpeechDetector {
    private val speech = mutableListOf<Float>()
    private var quiet = 0
    private var started = false
    var resets = 0

    override val hearingSpeech: Boolean get() = started && quiet == 0

    override fun accept(samples: FloatArray): FloatArray? {
        val loud = samples.any { kotlin.math.abs(it) > threshold }
        if (loud) {
            started = true
            quiet = 0
        } else if (started) {
            quiet++
        }
        if (started) speech += samples.toList()
        if (started && quiet >= silenceChunks) {
            val out = speech.toFloatArray()
            speech.clear()
            started = false
            quiet = 0
            return out
        }
        return null
    }

    override fun flush(): FloatArray? {
        if (!started) return null
        return speech.toFloatArray().also {
            speech.clear()
            started = false
        }
    }

    override fun reset() {
        resets++
        speech.clear()
        started = false
        quiet = 0
    }

    override fun close() {}
}

/** A microphone that plays back [chunks] (PCM16), then silence. */
class FakeAudioSource(private val chunks: List<ShortArray>) : AudioSource {
    var started = false
    var stopped = false
    private var next = 0

    override fun start() {
        started = true
    }

    override fun read(buffer: ShortArray): Int {
        val chunk = chunks.getOrNull(next++) ?: ShortArray(buffer.size)
        val n = minOf(chunk.size, buffer.size)
        chunk.copyInto(buffer, 0, 0, n)
        return n
    }

    override fun stop() {
        stopped = true
    }
}
