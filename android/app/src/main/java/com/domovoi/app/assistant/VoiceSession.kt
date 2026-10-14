package com.domovoi.app.assistant

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import kotlin.coroutines.CoroutineContext

/** Where a voice turn is. A tap in any state but [Idle] stops it (see
 *  [VoiceSession.tap]). */
enum class VoicePhase(val label: String) {
    Idle("idle"),
    Listening("listening"),
    Transcribing("transcribing"),
    Thinking("thinking"),
    Speaking("speaking"),
}

/** Audio focus for a turn: the phone's music pauses while the mic listens
 *  and ducks while Domovoi speaks (voice/ReplyPlayer.kt VoiceFocus). */
interface TurnFocus {
    fun capture()
    fun reply()
    fun release()

    object None : TurnFocus {
        override fun capture() {}
        override fun reply() {}
        override fun release() {}
    }
}

/** How one spoken turn went, in milliseconds. */
data class VoiceTiming(
    /** The voice detector deciding the speaker had finished, to the first
     *  word played: the plan's "voice turn" (target 3 s or less). */
    val toFirstWordMs: Long?,
    /** The silence the detector waits for before it decides, on top. */
    val silenceWaitMs: Long,
    /** Speech to text. */
    val sttMs: Long,
    /** The transcript ready to the model's first words. */
    val firstTextMs: Long?,
    /** The tap on stop to the voice silenced, when it was stopped. */
    val stopMs: Long? = null,
)

/**
 * A reply read aloud as it streams: [push] each delta, sentences go to the
 * voice as soon as they are complete ([SentenceSplitter], Markdown stripped),
 * the first while the model is still writing the rest. [stop] silences it.
 */
class SpokenReply(
    private val out: SpeechOut,
    private val clock: () -> Long,
    /** Called once, as the first sentence starts. */
    private val onSpeaking: () -> Unit,
) {
    private val splitter = SentenceSplitter()
    private val sentences = Channel<String>(Channel.UNLIMITED)
    @Volatile var firstAudioNs: Long? = null
        private set
    @Volatile private var stopped = false
    @Volatile var spokeAnything = false
        private set

    fun push(delta: String) {
        splitter.push(delta).forEach { sentences.trySend(it) }
    }

    /** The reply is complete: its last words are queued. */
    fun finish() {
        splitter.finish().forEach { sentences.trySend(it) }
        sentences.close()
    }

    /** Speaks every sentence queued, in order, until [finish]ed and drained
     *  or [stop]ped. The first goes alone, as soon as it is complete;
     *  sentences that arrived while one was being spoken go to the voice
     *  together, so it makes the next while it plays this one (no gap of a
     *  sentence's synthesis between them). */
    suspend fun run() {
        for (first in sentences) {
            if (stopped) break
            val batch = StringBuilder(first)
            while (true) {
                val more = sentences.tryReceive().getOrNull() ?: break
                batch.append(' ').append(more)
            }
            if (!spokeAnything) {
                spokeAnything = true
                onSpeaking()
            }
            val said = out.speak(batch.toString())
            if (firstAudioNs == null && said.firstAudioNs != null) firstAudioNs = said.firstAudioNs
        }
    }

    /** Silences it now; returns once the voice has been told. */
    fun stop() {
        stopped = true
        sentences.close()
        out.stop()
    }
}

/**
 * The push-to-talk turn on the phone (plan: VoiceSession):
 * Idle → Listening → Transcribing → Thinking → Speaking → Idle.
 *
 * - [tap] in Idle starts a turn: the mic opens only now (one owner at a
 *   time, audio/MicArbiter.kt, taken in MicCapture.listen), until the voice
 *   detector hears the end of speech.
 * - [tap] while Listening ends listening there: what was said so far is
 *   sent, or, before any speech, the turn ends with nothing sent.
 * - [tap] (or [stop]) in any later state stops it all at once: the voice is
 *   silenced, the model stopped (what it wrote stays), back to Idle.
 * - [answerAloud] reads a typed question's reply aloud (Thinking → Speaking
 *   → Idle) when the owner has spoken replies on for typed turns too.
 *
 * Audio is never written anywhere; only the transcript is kept, as the
 * question in the conversation. Problems (no permission, the mic in use,
 * nothing heard, nothing recognised, a voice that will not start) come out
 * of [problems] in words, and the session goes back to Idle.
 */
class VoiceSession(
    private val scope: CoroutineScope,
    /** Null when a turn may start; otherwise why not, in words. */
    private val blocker: () -> String?,
    /** Opens the mic for one utterance (MicCapture.listen with the voice
     *  detector); [onSpeech] when speech is first heard. Null: none heard. */
    private val listen: suspend (onSpeech: () -> Unit) -> Utterance?,
    /** Ends [listen] now with what it heard (MicCapture.stop). */
    private val endListening: () -> Unit,
    private val transcribe: suspend (FloatArray) -> String,
    /** Asks the model [transcript] (as the conversation's next question,
     *  marked spoken), streaming the reply's deltas to [onDelta]; returns
     *  how to file the turn's timing with the reply. */
    private val answer: suspend (transcript: String, onDelta: (String) -> Unit) -> (VoiceTiming) -> Unit,
    /** The voice that speaks (the phone's own pick, opened once). */
    private val voice: suspend () -> SpeechOut,
    /** Stops the model writing (the controller's engine). */
    private val stopModel: () -> Unit,
    private val focus: TurnFocus = TurnFocus.None,
    private val silenceWaitMs: Long = (SherpaSpeechDetector.DEFAULT_MIN_SILENCE_S * 1000).toLong(),
    private val work: CoroutineContext = Dispatchers.Default,
    private val clock: () -> Long = System::nanoTime,
) {
    private val _phase = MutableStateFlow(VoicePhase.Idle)
    val phase: StateFlow<VoicePhase> = _phase

    /** True once the mic has heard speech in this turn (a tap then sends). */
    private val _heardSpeech = MutableStateFlow(false)
    val heardSpeech: StateFlow<Boolean> = _heardSpeech

    private val _problems = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val problems: SharedFlow<String> = _problems

    /** The last stop of a reply being spoken: the tap to the voice silenced,
     *  ms (the gate's "stop within 200 ms"); null when the last turn was not
     *  stopped while speaking. */
    @Volatile var lastStopMs: Long? = null
        private set

    @Volatile private var job: Job? = null
    @Volatile private var speaking: SpokenReply? = null
    @Volatile private var stoppedAtNs: Long? = null

    val active: Boolean get() = _phase.value != VoicePhase.Idle

    /** The mic button. */
    fun tap() {
        when (_phase.value) {
            VoicePhase.Idle -> start()
            VoicePhase.Listening -> if (_heardSpeech.value) endListening() else stop()
            else -> stop()
        }
    }

    private fun start() {
        if (job?.isActive == true) return
        blocker()?.let {
            _problems.tryEmit(it)
            return
        }
        _heardSpeech.value = false
        lastStopMs = null
        _phase.value = VoicePhase.Listening
        job = scope.launch { runTurn { spokenTurn() } }
    }

    /** Reads [reply]'s deltas aloud as it streams (a typed turn with spoken
     *  replies on). Returns at once; the reply runs in the session. */
    fun answerAloud(reply: suspend (onDelta: (String) -> Unit) -> Unit) {
        if (job?.isActive == true) return
        lastStopMs = null
        _phase.value = VoicePhase.Thinking
        job = scope.launch {
            runTurn {
                // A voice that will not start still leaves the written reply.
                val spoken = openVoice()?.let { speakWith(it) }
                coroutineScope {
                    val speaker = spoken?.let { launch { it.run() } }
                    try {
                        reply { delta -> spoken?.push(delta) }
                    } finally {
                        spoken?.finish()
                    }
                    speaker?.join()
                }
            }
        }
    }

    /** Stops everything at once: listening, the model, the voice. */
    fun stop() {
        val t0 = clock()
        val wasSpeaking = _phase.value == VoicePhase.Speaking
        stoppedAtNs = t0
        endListening()
        speaking?.stop()
        val silencedNs = clock()
        stopModel()
        if (wasSpeaking) lastStopMs = (silencedNs - t0) / 1_000_000
        job?.cancel()
        _phase.value = VoicePhase.Idle
    }

    /** [stop], then waits for the turn to wind down (its reply filed). */
    suspend fun stopAndWait() {
        val running = job ?: return
        if (running.isActive) stop()
        running.join()
    }

    private suspend fun runTurn(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            problem(e.message ?: "the microphone stopped")
        } catch (e: Throwable) {
            problem("the voice turn failed: ${plainReason(e)}")
        } finally {
            speaking = null
            focus.release()
            _phase.value = VoicePhase.Idle
        }
    }

    private suspend fun spokenTurn() {
        stoppedAtNs = null
        focus.capture()
        val utterance = listen {
            _heardSpeech.value = true
        }
        focus.release()
        if (stoppedAtNs != null) return
        if (utterance == null) {
            problem("nothing was heard; tap the mic and ask again")
            return
        }
        _phase.value = VoicePhase.Transcribing
        val heardFrom = clock()
        val transcript = withContext(work) { transcribe(utterance.samples) }.trim()
        val heard = clock()
        if (transcript.isBlank() || transcript.none { it.isLetterOrDigit() }) {
            problem("didn't catch that; tap the mic and ask again")
            return
        }
        _phase.value = VoicePhase.Thinking
        // A voice that will not start still leaves the written answer.
        val spoken = openVoice()?.let { speakWith(it) }
        var firstText: Long? = null
        val file = coroutineScope {
            val speaker = spoken?.let { launch { it.run() } }
            val file = try {
                answer(transcript) { delta ->
                    if (firstText == null && delta.isNotBlank()) firstText = clock()
                    spoken?.push(delta)
                }
            } finally {
                spoken?.finish()
            }
            speaker?.join()
            file
        }
        file(
            VoiceTiming(
                toFirstWordMs = spoken?.firstAudioNs?.let { (it - utterance.endOfSpeechNs) / 1_000_000 },
                silenceWaitMs = if (utterance.endedBy == EndedBy.SILENCE) silenceWaitMs else 0,
                sttMs = (heard - heardFrom) / 1_000_000,
                firstTextMs = firstText?.let { (it - heard) / 1_000_000 },
            ),
        )
    }

    private suspend fun openVoice(): SpeechOut? = try {
        voice()
    } catch (e: CancellationException) {
        throw e
    } catch (e: Throwable) {
        problem("the voice could not start: ${plainReason(e)}")
        null
    }

    private fun speakWith(out: SpeechOut): SpokenReply =
        SpokenReply(out, clock) {
            focus.reply()
            if (_phase.value == VoicePhase.Thinking) _phase.value = VoicePhase.Speaking
        }.also { speaking = it }

    private fun problem(text: String) {
        _problems.tryEmit(text)
    }
}
