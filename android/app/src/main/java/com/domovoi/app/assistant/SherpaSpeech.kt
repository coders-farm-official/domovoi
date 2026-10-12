package com.domovoi.app.assistant

import com.k2fsa.sherpa.onnx.OfflineModelConfig
import com.k2fsa.sherpa.onnx.OfflineMoonshineModelConfig
import com.k2fsa.sherpa.onnx.OfflineRecognizer
import com.k2fsa.sherpa.onnx.OfflineRecognizerConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import java.io.File

// sherpa-onnx (k2-fsa) engines: listening only. The app's copy of
// sherpa-onnx is built without text to speech (android/native/sherpa-onnx);
// the phone speaks with Android's TextToSpeech (AndroidSpeechOut.kt).
// Every model is a downloaded file read by path (no AssetManager).
// sherpa-onnx ends the process on a config it cannot use, so each
// constructor checks its files exist first.

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
