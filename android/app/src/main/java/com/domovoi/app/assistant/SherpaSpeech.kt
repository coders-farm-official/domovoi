package com.domovoi.app.assistant

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineMoonshineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsVitsModelConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.File

// sherpa-onnx (k2-fsa) engines. Every model is a downloaded file read by
// path (no AssetManager). sherpa-onnx ends the process on a config it cannot
// use, so each constructor checks its files exist first.

private fun requireFiles(vararg paths: String) {
    paths.forEach { require(File(it).isFile) { "missing model file: $it" } }
}

/** Speech to text with a Moonshine (v2) model through sherpa-onnx. */
class SherpaSpeechToText(encoder: String, mergedDecoder: String, tokens: String, threads: Int = 2) : SpeechToText {
    private val recognizer: OfflineRecognizer

    init {
        requireFiles(encoder, mergedDecoder, tokens)
        recognizer = OfflineRecognizer(
            config = OfflineRecognizerConfig(
                modelConfig = OfflineModelConfig(
                    moonshine = OfflineMoonshineModelConfig(encoder = encoder, mergedDecoder = mergedDecoder),
                    tokens = tokens,
                    numThreads = threads,
                ),
            ),
        )
    }

    override fun transcribe(samples: FloatArray): String {
        val stream = recognizer.createStream()
        try {
            stream.acceptWaveform(samples, SPEECH_SAMPLE_RATE)
            recognizer.decode(stream)
            return recognizer.getResult(stream).text.trim()
        } finally {
            stream.release()
        }
    }

    override fun close() = recognizer.release()

    companion object {
        fun from(store: ModelStore, model: CatalogModel) = SherpaSpeechToText(
            encoder = store.path(model, "encoder_model.ort"),
            mergedDecoder = store.path(model, "decoder_model_merged.ort"),
            tokens = store.path(model, "tokens.txt"),
        )
    }
}

/**
 * End of speech with Silero VAD through sherpa-onnx. The utterance is
 * finished once speech has been followed by [minSilenceS] of silence; that
 * wait is part of every voice turn (the benchmark reports it).
 */
class SherpaSpeechDetector(model: String, val minSilenceS: Float = DEFAULT_MIN_SILENCE_S) : SpeechDetector {
    private val vad: Vad

    init {
        requireFiles(model)
        vad = Vad(
            config = VadModelConfig(
                sileroVadModelConfig = SileroVadModelConfig(
                    model = model,
                    threshold = 0.5f,
                    minSilenceDuration = minSilenceS,
                    minSpeechDuration = 0.25f,
                    windowSize = 512,
                    maxSpeechDuration = 30f,
                ),
                sampleRate = SPEECH_SAMPLE_RATE,
                numThreads = 1,
            ),
        )
    }

    override val hearingSpeech: Boolean get() = vad.isSpeechDetected()

    override fun accept(samples: FloatArray): FloatArray? {
        vad.acceptWaveform(samples)
        return takeSegment()
    }

    override fun flush(): FloatArray? {
        vad.flush()
        return takeSegment()
    }

    override fun reset() = vad.reset()

    override fun close() = vad.release()

    private fun takeSegment(): FloatArray? {
        if (vad.empty()) return null
        val segment = vad.front().samples
        vad.clear()
        return segment
    }

    companion object {
        const val DEFAULT_MIN_SILENCE_S = 0.5f

        fun from(store: ModelStore) = SherpaSpeechDetector(store.path(ModelCatalog.sileroVad, "silero_vad.onnx"))
    }
}

/**
 * Speech out with a Piper (VITS) voice through sherpa-onnx, played through
 * an AudioTrack marked as the assistant speaking. Each sentence is
 * synthesized, then streamed to the speaker; [stop] aborts synthesis and
 * silences playback at once.
 */
class PiperSpeechOut(model: String, tokens: String, dataDir: String, threads: Int = 2) : SpeechOut {
    private val tts: OfflineTts
    val sampleRate: Int
    private val track: AudioTrack
    @Volatile private var stopped = false

    init {
        requireFiles(model, tokens, "$dataDir/phontab", "$dataDir/phonindex", "$dataDir/phondata", "$dataDir/intonations")
        tts = OfflineTts(
            config = OfflineTtsConfig(
                model = OfflineTtsModelConfig(
                    vits = OfflineTtsVitsModelConfig(model = model, tokens = tokens, dataDir = dataDir),
                    numThreads = threads,
                ),
            ),
        )
        sampleRate = tts.sampleRate()
        val minBuffer = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_FLOAT)
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(maxOf(minBuffer, sampleRate * 4 / 4))
            .build()
    }

    /** Synthesis only, no playback (the benchmark's spoken self-test). */
    fun synthesize(text: String): FloatArray = tts.generate(text, 0, 1f).samples

    override suspend fun speak(sentence: String): SpokenSentence = withContext(Dispatchers.IO) {
        stopped = false
        val started = System.nanoTime()
        var firstAudio: Long? = null
        var frames = 0L
        val headAtStart = track.playbackHeadPosition.toLong() and 0xFFFFFFFFL
        track.play()
        tts.generateWithCallback(sentence, 0, 1f) { chunk ->
            if (stopped) return@generateWithCallback 0
            if (firstAudio == null) firstAudio = System.nanoTime()
            track.write(chunk, 0, chunk.size, AudioTrack.WRITE_BLOCKING)
            frames += chunk.size
            if (stopped) 0 else 1
        }
        // Wait for the speaker to play what was written (or a stop).
        val deadline = System.nanoTime() + ((frames * 1_000_000_000L) / sampleRate) + 1_000_000_000L
        while (!stopped && System.nanoTime() < deadline) {
            val played = (track.playbackHeadPosition.toLong() and 0xFFFFFFFFL) - headAtStart
            if (played >= frames) break
            delay(15)
        }
        if (!stopped) track.pause()
        SpokenSentence(sentence, started, firstAudio, System.nanoTime(), frames.toDouble() / sampleRate)
    }

    override fun stop() {
        stopped = true
        runCatching {
            track.pause()
            track.flush()
        }
    }

    override fun close() {
        stop()
        runCatching { track.release() }
        tts.release()
    }

    companion object {
        fun from(store: ModelStore): PiperSpeechOut {
            val voice = ModelCatalog.piperTimingVoice
            return PiperSpeechOut(
                model = store.path(voice, "en_US-lessac-medium.onnx"),
                tokens = store.path(voice, "tokens.txt"),
                dataDir = store.path(voice, "espeak-ng-data"),
            )
        }
    }
}
