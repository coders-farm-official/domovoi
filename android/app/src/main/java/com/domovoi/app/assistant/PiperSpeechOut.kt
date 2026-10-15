package com.domovoi.app.assistant

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import com.domovoi.app.phonemizer.PhonemeIds
import com.domovoi.app.phonemizer.Phonemizer
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.atomic.AtomicLong

/** Where synthesized PCM goes: the speaker ([AudioTrackSink]), or a fake in tests. */
interface PcmSink : AutoCloseable {
    val sampleRate: Int

    /** Silence written after the last sentence so the speaker plays the
     *  stream's end instead of holding it in its buffer. */
    val tailFrames: Int

    /** Starts playback (with nothing yet); [write] does nothing until then. */
    fun start()

    /** Hands [pcm] to the speaker. Blocks while the speaker's buffer is
     *  full; returns at once after [stop]. */
    fun write(pcm: ShortArray)

    /** Frames played since the last [stop] (or since it was made). */
    fun playedFrames(): Long

    /** Silences it at once and drops what is buffered. */
    fun stop()
}

/**
 * The speaker as an [AudioTrack] stream: 16-bit mono at the voice's rate,
 * played as the assistant speaking (USAGE_ASSISTANT), as AndroidSpeechOut
 * does. Made once and reused for every sentence.
 */
class AudioTrackSink(override val sampleRate: Int) : PcmSink {
    private val track: AudioTrack
    @Volatile private var playing = false

    /** What the track holds before it plays: about 100 ms. */
    private val bufferFrames: Int

    override val tailFrames: Int get() = bufferFrames * 2

    init {
        val min = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val bytes = maxOf(min, sampleRate / 10 * 2)
        bufferFrames = bytes / 2
        track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(sampleRate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setBufferSizeInBytes(bytes)
            .build()
    }

    override fun start() {
        track.play()
        playing = true
    }

    override fun write(pcm: ShortArray) {
        var at = 0
        while (at < pcm.size && playing) {
            val n = track.write(pcm, at, pcm.size - at)
            if (n <= 0) break
            at += n
        }
    }

    override fun playedFrames(): Long = track.playbackHeadPosition.toLong() and 0xFFFFFFFFL

    override fun stop() {
        playing = false
        runCatching {
            track.pause()
            track.flush()
        }
    }

    override fun close() {
        stop()
        runCatching { track.release() }
    }
}

/**
 * Speech out with Domovoi's own voice on the phone: the Kotlin port of the
 * server's text-to-phoneme converter ([Phonemizer]) and a Piper voice on
 * ONNX Runtime ([VoiceModel]). Text is cut into sentences by the
 * converter, each synthesized and played as soon as it is ready while the
 * next is being synthesized, with Piper's 0.2 s between sentences. [stop]
 * silences it at once and abandons the synthesis in progress.
 */
class PiperSpeechOut(
    private val phonemizer: Phonemizer,
    private val voice: VoiceModel,
    /** The catalog id of the voice, e.g. en_US-norman-medium. */
    override val voiceKey: String,
    private val sink: PcmSink,
    private val idMap: Map<String, IntArray> = phonemizer.idMap,
    private val threads: Int = 0,
    private val clock: () -> Long = System::nanoTime,
    private val log: (String) -> Unit = { Log.i(TAG, it) },
    /** The voice's phoneme set ("en", or "en-us" for a voice trained on
     *  espeak's American English): PiperConfig.dialect. */
    private val dialect: String = Phonemizer.EN,
) : PhoneSpeech {

    private val generation = AtomicLong()

    /** Symbols the converter writes that the voice's map lacks -> what is
     *  said instead (PhonemeIds.fit). */
    private val missing = HashMap<String, String>()

    /** One sentence's ids for this voice, a symbol it lacks said as the
     *  nearest one it has (logged once); null when nothing in it is a sound
     *  the voice knows (it is skipped, never sent). */
    internal fun idsFor(phonemes: String): IntArray? {
        val seen = synchronized(missing) { missing.keys.toSet() }
        val ids = PhonemeIds.ids(PhonemeIds.fit(phonemes, idMap, missing), idMap)
        val new = synchronized(missing) { missing.filterKeys { it !in seen } }
        if (new.isNotEmpty()) log("the voice's map has no ${new.keys.joinToString()}; said as ${new.values.joinToString { it.ifEmpty { "nothing" } }}")
        return ids.takeIf { PhonemeIds.speaksAnything(it, idMap) }
    }

    /** (runs away, the lead-in's phonemes, its length alone), measured once,
     *  on the first short sentence: see [LeadIn]. */
    private val leadIn: Triple<Boolean, String, Double> by lazy {
        val longest = LeadIn.PROBES.maxOf { word ->
            idsFor(phonemizer.phonemize(word, dialect).joinToString(" "))
                ?.let { voice.synthesize(it).size.toDouble() / voice.sampleRate } ?: 0.0
        }
        val runsAway = longest > LeadIn.RUNAWAY_SECONDS
        val lead = phonemizer.phonemize(LeadIn.TEXT, dialect).joinToString(" ")
        val seconds = if (runsAway) {
            idsFor(lead)?.let { voice.synthesize(it).size.toDouble() / voice.sampleRate } ?: 0.0
        } else 0.0
        if (runsAway) log(String.format(java.util.Locale.US,
            "%s says a short sentence alone in %.1f s; its short sentences are said after a lead-in", voiceKey, longest))
        Triple(runsAway, lead, seconds)
    }

    /** One sentence's audio, null when nothing in it is a sound the voice
     *  knows; a short sentence in a voice that runs away is said after the
     *  lead-in and cut off from it ([LeadIn]). Throws [VoiceModel.Stopped]. */
    internal fun sentenceAudio(phonemes: String): FloatArray? {
        val ids = idsFor(phonemes) ?: return null
        if (LeadIn.phonemeCount(phonemes) < LeadIn.MAX_PHONEMES) {
            val (runsAway, lead, leadSeconds) = leadIn
            if (runsAway && leadSeconds > 0) {
                val both = idsFor("$lead $phonemes")
                if (both != null) {
                    repeat(LeadIn.TRIES) {
                        LeadIn.cut(voice.synthesize(both), voice.sampleRate, leadSeconds)
                            ?.let { return PiperVoice.peakNormalize(it) }
                    }
                }
                log("no pause after the lead-in; the sentence is said alone")
            }
        }
        return voice.synthesize(ids)
    }

    /** The last sentence's phoneme strings, as spoken (for the device check). */
    @Volatile var lastPhonemes: List<String> = emptyList()
        private set

    override fun describe(): String =
        "$DOMOVOI_VOICE $voiceKey (Piper VITS, ${voice.sampleRate} Hz) on ONNX Runtime" +
            (if (threads > 0) ", $threads threads" else "") +
            "; phonemes from Domovoi's converter (Kotlin port), on the phone"

    override suspend fun speak(sentence: String): SpokenSentence = withContext(Dispatchers.IO) {
        val gen = generation.get()
        val started = clock()
        val phonemes = phonemizer.phonemize(sentence, dialect)
        lastPhonemes = phonemes
        log("phonemes: ${phonemes.joinToString(" | ")}")
        var first: Long? = null
        var frames = 0L
        try {
            if (generation.get() == gen) sink.start()
            coroutineScope {
                val chunks = Channel<ShortArray>(capacity = 2)
                val producer = launch(Dispatchers.Default) {
                    try {
                        for ((i, p) in phonemes.withIndex()) {
                            if (generation.get() != gen) break
                            val t0 = clock()
                            val audio = try {
                                sentenceAudio(p) ?: continue
                            } catch (e: VoiceModel.Stopped) {
                                break
                            }
                            val ms = (clock() - t0) / 1_000_000
                            val seconds = audio.size.toDouble() / voice.sampleRate
                            log(String.format(java.util.Locale.US, "sentence %d of %d: %d ms for %.2f s of audio (real-time factor %.3f)",
                                i + 1, phonemes.size, ms, seconds, if (seconds > 0) ms / 1000.0 / seconds else 0.0))
                            chunks.send(toPcm16(audio))
                        }
                    } finally {
                        chunks.close()
                    }
                }
                var k = 0
                for (pcm in chunks) {
                    if (generation.get() != gen) break
                    if (k++ > 0) {
                        val gap = ShortArray((voice.sampleRate * SENTENCE_GAP_S).toInt())
                        sink.write(gap)
                        frames += gap.size
                    }
                    if (first == null) first = clock()
                    sink.write(pcm)
                    frames += pcm.size
                }
                producer.cancel()
            }
            if (generation.get() == gen && frames > 0) awaitPlayed(frames, gen)
        } catch (e: CancellationException) {
            stop()
            throw e
        } finally {
            if (generation.get() == gen) sink.stop()
        }
        SpokenSentence(sentence, started, first, clock(), frames.toDouble() / voice.sampleRate)
    }

    /** Waits until the speaker has played [frames] (the stream's tail is
     *  pushed out with silence first), or [stop] was called. */
    private suspend fun awaitPlayed(frames: Long, gen: Long) {
        sink.write(ShortArray(sink.tailFrames))
        val deadline = clock() + ((frames * 1_000_000_000L) / voice.sampleRate) + 3_000_000_000L
        while (generation.get() == gen && sink.playedFrames() < frames && clock() < deadline) delay(10)
    }

    /** The voice's audio for [text], every sentence with the gap between
     *  them, not played (the benchmark's self-test). */
    override suspend fun synthesize(text: String): SynthesizedSpeech = withContext(Dispatchers.Default) {
        val parts = phonemizer.phonemize(text, dialect).mapNotNull { p -> sentenceAudio(p) }
        val gap = (voice.sampleRate * SENTENCE_GAP_S).toInt()
        val out = FloatArray(parts.sumOf { it.size } + gap * (parts.size - 1).coerceAtLeast(0))
        var at = 0
        parts.forEachIndexed { i, a ->
            if (i > 0) at += gap
            a.copyInto(out, at)
            at += a.size
        }
        SynthesizedSpeech(out, voice.sampleRate)
    }

    override fun stop() {
        generation.incrementAndGet()
        voice.cancel()
        sink.stop()
    }

    /** Releases the speaker; the voice itself is shared and stays loaded. */
    override fun close() {
        stop()
        sink.close()
    }

    companion object {
        private const val TAG = "DomovoiVoice"

        /** Silence between sentences, Piper's own default. */
        const val SENTENCE_GAP_S = 0.2

        fun toPcm16(audio: FloatArray): ShortArray =
            ShortArray(audio.size) { (audio[it].coerceIn(-1f, 1f) * 32767f).toInt().toShort() }
    }
}
