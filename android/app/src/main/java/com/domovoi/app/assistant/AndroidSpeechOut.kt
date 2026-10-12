package com.domovoi.app.assistant

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong

/** A voice as the engine lists it, reduced to what choosing one needs. */
data class VoiceChoice(
    val name: String,
    /** ISO 639 language ("en"), as Locale.getLanguage() gives it. */
    val language: String,
    /** ISO 3166 country ("US"), or "". */
    val country: String,
    /** Voice.QUALITY_*: 100 very low … 500 very high. */
    val quality: Int,
    /** Voice.LATENCY_*: 100 very low … 500 very high. */
    val latency: Int,
    val needsNetwork: Boolean,
    val notInstalled: Boolean,
)

/**
 * The voice to speak with: one that runs on the phone (never one that sends
 * the text to a server) and is installed, in [language]; the [country]
 * variant first, then the best quality, then the quickest. Null when the
 * engine has none.
 */
fun pickVoice(voices: List<VoiceChoice>, language: String, country: String): VoiceChoice? =
    voices.filter { !it.needsNetwork && !it.notInstalled && it.language.equals(language, ignoreCase = true) }
        .sortedWith(
            compareByDescending<VoiceChoice> { it.country.equals(country, ignoreCase = true) }
                .thenByDescending { it.quality }
                .thenBy { it.latency }
                .thenBy { it.name },
        )
        .firstOrNull()

/** Voice.QUALITY_* / Voice.LATENCY_* as words. */
fun voiceLevelName(level: Int): String = when (level) {
    100 -> "very low"
    200 -> "low"
    300 -> "normal"
    400 -> "high"
    500 -> "very high"
    else -> "level $level"
}

/** Which engine and voice speak, for the benchmark's results. */
data class SpeechVoiceInfo(
    val engineLabel: String,
    val enginePackage: String,
    val voice: VoiceChoice,
    /** Whether the engine reports each word as it is spoken (onRangeStart);
     *  null until something has been spoken. */
    val wordTiming: Boolean?,
) {
    fun describe(): String = buildString {
        append("Android TextToSpeech, engine $engineLabel ($enginePackage); voice ${voice.name} ")
        append("(${voice.language}${if (voice.country.isNotEmpty()) "-${voice.country}" else ""}, ")
        append("${voiceLevelName(voice.quality)} quality, ${voiceLevelName(voice.latency)} latency, on the phone)")
        when (wordTiming) {
            true -> append("; first word timed by the engine's word callback")
            false -> append("; this engine reports no words, so the first word is timed from the start of playback")
            null -> {}
        }
    }
}

/**
 * Speech out with the phone's own text-to-speech engine
 * (android.speech.tts.TextToSpeech): the user's default engine, with a voice
 * that runs on the phone ([pickVoice]), played as the assistant speaking
 * (USAGE_ASSISTANT). Sentences queue one after another; [stop] silences the
 * one playing and drops the rest.
 *
 * The first spoken word is the engine's first onRangeStart for the sentence
 * (called as the audio for that word reaches the speaker), or onStart when
 * the engine reports no words.
 */
class AndroidSpeechOut private constructor(
    private val app: Context,
    private val tts: TextToSpeech,
    private val enginePackage: String,
    private val engineLabel: String,
    val voice: VoiceChoice,
) : PhoneSpeech {

    override val voiceKey: String get() = VoiceSource.ANDROID

    override fun describe(): String = info.describe()

    private class InFlight {
        /** null: finished; [STOPPED]; anything else is the engine's error code. */
        val done = CompletableDeferred<Int?>()
        @Volatile var startNs: Long? = null
        @Volatile var firstWordNs: Long? = null
        @Volatile var bytes = 0L
        @Volatile var bytesPerSecond = 0
    }

    private val inFlight = ConcurrentHashMap<String, InFlight>()
    private val ids = AtomicLong()
    @Volatile private var sawWord: Boolean? = null

    val info: SpeechVoiceInfo get() = SpeechVoiceInfo(engineLabel, enginePackage, voice, sawWord)

    /** The utterance a callback is about (the engine may pass a null id). */
    private fun job(utteranceId: String?): InFlight? = utteranceId?.let { inFlight[it] }

    private val listener = object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {
            job(utteranceId)?.let { if (it.startNs == null) it.startNs = System.nanoTime() }
        }

        override fun onBeginSynthesis(utteranceId: String?, sampleRateInHz: Int, audioFormat: Int, channelCount: Int) {
            val width = when (audioFormat) {
                AudioFormat.ENCODING_PCM_8BIT -> 1
                AudioFormat.ENCODING_PCM_FLOAT -> 4
                else -> 2
            }
            job(utteranceId)?.bytesPerSecond = sampleRateInHz * width * channelCount.coerceAtLeast(1)
        }

        override fun onAudioAvailable(utteranceId: String?, audio: ByteArray?) {
            job(utteranceId)?.let { it.bytes += audio?.size ?: 0 }
        }

        override fun onRangeStart(utteranceId: String?, start: Int, end: Int, frame: Int) {
            val said = job(utteranceId) ?: return
            if (said.firstWordNs == null) said.firstWordNs = System.nanoTime()
            sawWord = true
        }

        override fun onDone(utteranceId: String?) {
            job(utteranceId)?.done?.complete(null)
        }

        override fun onStop(utteranceId: String?, interrupted: Boolean) {
            job(utteranceId)?.done?.complete(STOPPED)
        }

        @Deprecated("Deprecated in Java")
        override fun onError(utteranceId: String?) {
            job(utteranceId)?.done?.complete(TextToSpeech.ERROR)
        }

        override fun onError(utteranceId: String?, errorCode: Int) {
            job(utteranceId)?.done?.complete(errorCode)
        }
    }

    override suspend fun speak(sentence: String): SpokenSentence {
        val id = "say-${ids.incrementAndGet()}"
        val job = InFlight().also { inFlight[id] = it }
        val started = System.nanoTime()
        try {
            check(tts.speak(sentence, TextToSpeech.QUEUE_ADD, Bundle(), id) == TextToSpeech.SUCCESS) {
                "the text-to-speech engine refused the sentence"
            }
            val outcome = job.done.await()
            if (outcome != null && outcome != STOPPED) error("text to speech failed (${errorName(outcome)})")
            if (outcome == null && job.firstWordNs == null && sawWord == null) sawWord = false
            val seconds = if (job.bytesPerSecond > 0) job.bytes.toDouble() / job.bytesPerSecond else 0.0
            return SpokenSentence(sentence, started, job.firstWordNs ?: job.startNs, System.nanoTime(), seconds)
        } catch (e: CancellationException) {
            tts.stop()
            throw e
        } finally {
            inFlight.remove(id)
        }
    }

    /** The engine's audio for [text] (through a WAV file in the cache), not played. */
    override suspend fun synthesize(text: String): SynthesizedSpeech = withContext(Dispatchers.IO) {
        val file = File.createTempFile("tts-", ".wav", app.cacheDir)
        val id = "file-${ids.incrementAndGet()}"
        val job = InFlight().also { inFlight[id] = it }
        try {
            check(tts.synthesizeToFile(text, Bundle(), file, id) == TextToSpeech.SUCCESS) {
                "the text-to-speech engine refused to write audio"
            }
            val outcome = withTimeout(SYNTH_TIMEOUT_MS) { job.done.await() }
            if (outcome != null) error("text to speech failed (${errorName(outcome)})")
            parseWav(file.readBytes()) ?: error("the engine's audio file could not be read")
        } finally {
            inFlight.remove(id)
            file.delete()
        }
    }

    override fun stop() {
        runCatching { tts.stop() }
        inFlight.values.forEach { it.done.complete(STOPPED) }
    }

    override fun close() {
        stop()
        runCatching { tts.shutdown() }
    }

    companion object {
        private const val STOPPED = Int.MIN_VALUE
        private const val INIT_TIMEOUT_MS = 10_000L
        private const val SYNTH_TIMEOUT_MS = 30_000L

        /**
         * Binds the phone's default text-to-speech engine and picks its
         * voice. Throws when the phone has no engine, or no English voice
         * that runs on the phone.
         */
        suspend fun create(context: Context): AndroidSpeechOut {
            val app = context.applicationContext
            val ready = CompletableDeferred<Int>()
            val tts = withContext(Dispatchers.Main) { TextToSpeech(app) { status -> ready.complete(status) } }
            val status = withTimeoutOrNull(INIT_TIMEOUT_MS) { ready.await() }
            if (status != TextToSpeech.SUCCESS) {
                runCatching { tts.shutdown() }
                error(
                    if (status == null) "the text-to-speech engine did not start in ${INIT_TIMEOUT_MS / 1000} s"
                    else "this phone has no working text-to-speech engine",
                )
            }
            return try {
                withContext(Dispatchers.IO) { configure(app, tts) }
            } catch (e: Throwable) {
                runCatching { tts.shutdown() }
                throw e
            }
        }

        private fun configure(app: Context, tts: TextToSpeech): AndroidSpeechOut {
            val enginePackage = tts.defaultEngine.orEmpty()
            val engineLabel = tts.engines.orEmpty().firstOrNull { it.name == enginePackage }?.label ?: enginePackage
            val voices = tts.voices.orEmpty().toList()
            val choices = voices.map { v ->
                VoiceChoice(
                    name = v.name,
                    language = v.locale.language,
                    country = v.locale.country,
                    quality = v.quality,
                    latency = v.latency,
                    needsNetwork = v.isNetworkConnectionRequired,
                    notInstalled = TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED in v.features.orEmpty(),
                )
            }
            // The assistant answers in English: the phone's own English
            // variant when it has one, else US English.
            val phone = Locale.getDefault()
            val country = if (phone.language == "en" && phone.country.isNotEmpty()) phone.country else "US"
            val chosen = pickVoice(choices, "en", country)
                ?: error("no English voice that runs on the phone is installed (the engine lists ${voices.size}); " +
                    "add one under Android Settings, Text-to-speech output")
            check(tts.setVoice(voices.first { it.name == chosen.name }) == TextToSpeech.SUCCESS) {
                "the engine would not use voice ${chosen.name}"
            }
            tts.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            return AndroidSpeechOut(app, tts, enginePackage, engineLabel, chosen).also {
                tts.setOnUtteranceProgressListener(it.listener)
            }
        }

        private fun errorName(code: Int): String = when (code) {
            TextToSpeech.ERROR_SYNTHESIS -> "synthesis error"
            TextToSpeech.ERROR_SERVICE -> "engine service error"
            TextToSpeech.ERROR_OUTPUT -> "audio output error"
            TextToSpeech.ERROR_NETWORK -> "network error"
            TextToSpeech.ERROR_NETWORK_TIMEOUT -> "network timeout"
            TextToSpeech.ERROR_INVALID_REQUEST -> "invalid request"
            TextToSpeech.ERROR_NOT_INSTALLED_YET -> "voice not installed yet"
            else -> "error $code"
        }
    }
}
