package com.domovoi.app.assistant

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

// The benchmark's measured runs, written against the engine interfaces so
// they run on the JVM with fakes. Every time is a System.nanoTime()-style
// reading from [clock]; results are in milliseconds.

private fun msBetween(fromNs: Long, toNs: Long): Long = (toNs - fromNs) / 1_000_000

/** One typed question in a fresh conversation, timed from send. */
suspend fun runTextTurn(
    llm: LlmEngine,
    system: String,
    prompt: String,
    clock: () -> Long = System::nanoTime,
    onText: (String) -> Unit = {},
): TextTurnResult {
    val sent = clock()
    var first: Long? = null
    val reply = StringBuilder()
    llm.reply(system, listOf(ChatTurn(fromUser = true, text = prompt))).collect { delta ->
        if (first == null && delta.isNotBlank()) first = clock()
        reply.append(delta)
        onText(reply.toString())
    }
    val done = clock()
    return TextTurnResult(prompt, first?.let { msBetween(sent, it) }, msBetween(sent, done), llm.lastStats(), reply.toString())
}

/**
 * One voice turn after the question has been heard: speech to text, the
 * model's reply split into sentences as it streams, each sentence spoken as
 * soon as it is complete (the first while the model is still writing). The
 * whole reply is spoken; the timings stop at the first spoken word.
 */
suspend fun runVoiceTurn(
    utterance: Utterance,
    source: String,
    sttModel: String,
    endOfSpeechWaitMs: Long,
    stt: SpeechToText,
    llm: LlmEngine,
    voice: SpeechOut,
    system: String,
    clock: () -> Long = System::nanoTime,
    onText: (String) -> Unit = {},
): VoiceTurnResult = coroutineScope {
    val endOfSpeech = utterance.endOfSpeechNs
    val transcript = withContext(Dispatchers.Default) { stt.transcribe(utterance.samples) }
    val heard = clock()
    fun result(
        reply: String = "",
        firstText: Long? = null,
        firstSentence: Long? = null,
        firstAudio: Long? = null,
        error: String? = null,
    ) = VoiceTurnResult(
        source = source,
        sttModel = sttModel,
        utteranceS = utterance.seconds,
        endOfSpeechWaitMs = endOfSpeechWaitMs,
        transcript = transcript,
        sttMs = msBetween(endOfSpeech, heard),
        llmFirstTextMs = firstText?.let { msBetween(heard, it) },
        firstSentenceMs = firstSentence?.let { msBetween(heard, it) },
        ttsFirstAudioMs = if (firstSentence != null && firstAudio != null) msBetween(firstSentence, firstAudio) else null,
        toFirstAudioMs = firstAudio?.let { msBetween(endOfSpeech, it) },
        reply = reply,
        error = error,
    )
    if (transcript.isBlank()) return@coroutineScope result(error = "nothing recognizable was heard")

    val sentences = Channel<String>(Channel.UNLIMITED)
    var firstAudio: Long? = null
    val speaker = launch {
        for (sentence in sentences) {
            val spoken = voice.speak(sentence)
            if (firstAudio == null) firstAudio = spoken.firstAudioNs
        }
    }
    val splitter = SentenceSplitter()
    var firstText: Long? = null
    var firstSentence: Long? = null
    val reply = StringBuilder()
    suspend fun queue(found: List<String>) {
        for (s in found) {
            if (firstSentence == null) firstSentence = clock()
            sentences.send(s)
        }
    }
    try {
        llm.reply(system, listOf(ChatTurn(fromUser = true, text = transcript))).collect { delta ->
            if (firstText == null && delta.isNotBlank()) firstText = clock()
            reply.append(delta)
            onText(reply.toString())
            queue(splitter.push(delta))
        }
        queue(splitter.finish())
    } finally {
        sentences.close()
    }
    speaker.join()
    result(reply.toString(), firstText, firstSentence, firstAudio)
}

/**
 * The spoken self-test's "microphone": [speech] (16 kHz) between short
 * silences, fed to [detector] in microphone-sized chunks, so the detector
 * decides where the utterance ends exactly as it would live.
 */
fun detectUtterance(
    speech: FloatArray,
    detector: SpeechDetector,
    clock: () -> Long = System::nanoTime,
): Utterance? {
    detector.reset()
    val lead = SPEECH_SAMPLE_RATE / 4
    val tail = SPEECH_SAMPLE_RATE * 3 / 2
    val padded = FloatArray(lead + speech.size + tail).also { speech.copyInto(it, lead) }
    var start: Long? = null
    var offset = 0
    while (offset < padded.size) {
        val end = minOf(offset + MicCapture.CHUNK_SAMPLES, padded.size)
        val finished = detector.accept(padded.copyOfRange(offset, end))
        if (start == null && (detector.hearingSpeech || finished != null)) start = clock()
        if (finished != null) return Utterance(finished, start, clock(), EndedBy.SILENCE)
        offset = end
    }
    return detector.flush()?.let { Utterance(it, start, clock(), EndedBy.LENGTH_CAP) }
}

/** What the heat loop reads from the phone between replies. */
data class PhoneReading(val batteryTempC: Double?, val thermalStatus: Int?, val online: Boolean)

/**
 * Replies back to back for [minutes] (fresh conversation each time, cycling
 * [prompts]), reading decode speed and temperature after each one. [stop]
 * ends it between replies (cancel the engine to end the current one).
 */
suspend fun runHeatLoop(
    llm: LlmEngine,
    system: String,
    prompts: List<String>,
    minutes: Int,
    read: () -> PhoneReading,
    stop: () -> Boolean,
    clock: () -> Long = System::nanoTime,
    onSample: (HeatSample) -> Unit = {},
): HeatResult {
    require(prompts.isNotEmpty())
    val started = clock()
    val limitNs = minutes * 60L * 1_000_000_000L
    val samples = mutableListOf<HeatSample>()
    var turn = 0
    while (clock() - started < limitNs) {
        currentCoroutineContext().ensureActive()
        if (stop()) return HeatResult(minutes, samples, stoppedEarly = true)
        val result = try {
            runTextTurn(llm, system, prompts[turn++ % prompts.size], clock)
        } catch (e: CancellationException) {
            currentCoroutineContext().ensureActive()
            if (stop()) return HeatResult(minutes, samples, stoppedEarly = true)
            throw e
        }
        val tps = result.stats?.decodeTokensPerS
            ?: (result.reply.length / CHARS_PER_TOKEN) / (result.totalMs.coerceAtLeast(1) / 1000.0)
        val phone = read()
        val sample = HeatSample(
            atS = (clock() - started) / 1e9,
            decodeTokensPerS = tps,
            batteryTempC = phone.batteryTempC,
            thermalStatus = phone.thermalStatus,
            online = phone.online,
        )
        samples += sample
        onSample(sample)
    }
    return HeatResult(minutes, samples, stoppedEarly = false)
}

/** Rough English average, only for an engine that keeps no counters. */
private const val CHARS_PER_TOKEN = 4.0
