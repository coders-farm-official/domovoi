package com.domovoi.app.assistant

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The push-to-talk turn's state machine (plan: VoiceSession): every
 * transition, a stop from every state, and the errors: no permission, the
 * mic held by another owner, nothing heard, an empty transcript, an engine
 * that fails, a voice that will not start. Each step is held on a gate so
 * the phase can be read while the turn is in it.
 */
class VoiceSessionTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val problems = CopyOnWriteArrayList<String>()
    private val focus = CopyOnWriteArrayList<String>()
    private val asked = CopyOnWriteArrayList<String>()
    private val timings = CopyOnWriteArrayList<VoiceTiming>()
    private val clock = TestClock()

    @Volatile private var blocker: String? = null
    @Volatile private var heardGate: CompletableDeferred<Unit>? = null
    @Volatile private var transcribeGate: CompletableDeferred<Unit>? = null
    @Volatile private var answerGate: CompletableDeferred<Unit>? = null
    @Volatile private var utterance: Utterance? = Utterance(FloatArray(16_000), 0, 0, EndedBy.SILENCE)
    @Volatile private var micFails: IOException? = null
    @Volatile private var transcript = "what is the tallest mountain"
    @Volatile private var replyDeltas = listOf("Mount Everest", " is the tallest. ", "It is in Nepal.")
    @Volatile private var replyGate: CompletableDeferred<Unit>? = null
    @Volatile private var replyFails: Throwable? = null
    @Volatile private var voiceFails: Throwable? = null
    @Volatile private var ended = 0
    @Volatile private var modelStops = 0
    private val out = GatedSpeechOut(clock)

    @After fun tearDown() {
        scope.cancel()
    }

    private fun session() = VoiceSession(
        scope = scope,
        blocker = { blocker },
        listen = { onSpeech ->
            micFails?.let { throw it }
            onSpeech()
            heardGate?.await()
            clock.advanceMs(100)
            utterance?.let { Utterance(it.samples, it.speechStartedNs, clock.nowNs, it.endedBy) }
        },
        endListening = { ended++; heardGate?.complete(Unit) },
        transcribe = {
            transcribeGate?.await()
            clock.advanceMs(60)
            transcript
        },
        answer = { text, onDelta ->
            asked += text
            answerGate?.await()
            replyFails?.let { throw it }
            clock.advanceMs(400)
            replyDeltas.forEachIndexed { i, d ->
                onDelta(d)
                if (i == 0) replyGate?.await()
            }
            val file: (VoiceTiming) -> Unit = { timings += it }
            file
        },
        voice = { voiceFails?.let { throw it }; out },
        stopModel = { modelStops++ },
        focus = object : TurnFocus {
            override fun capture() { focus += "capture" }
            override fun reply() { focus += "reply" }
            override fun release() { focus += "release" }
        },
        clock = clock.read,
    ).also { s ->
        scope.launch { s.problems.collect { problems += it } }
        Thread.sleep(30)   // the collector is listening before the first tap
    }

    private fun until(what: String, timeoutMs: Long = 5_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what (problems $problems)")
            Thread.sleep(2)
        }
    }

    @Test fun aWholeTurnGoesThroughEveryPhaseAndSpeaksEverySentence() {
        heardGate = CompletableDeferred()
        transcribeGate = CompletableDeferred()
        answerGate = CompletableDeferred()
        replyGate = CompletableDeferred()
        replyDeltas = listOf("Mount Everest is the tallest. ", "It is in Nepal.")
        val s = session()
        Thread.sleep(20)
        assertEquals(VoicePhase.Idle, s.phase.value)
        s.tap()
        until("listening, hearing you") { s.phase.value == VoicePhase.Listening && s.heardSpeech.value }
        assertEquals(listOf("capture"), focus.toList())
        heardGate!!.complete(Unit)
        until("transcribing") { s.phase.value == VoicePhase.Transcribing }
        transcribeGate!!.complete(Unit)
        until("thinking") { s.phase.value == VoicePhase.Thinking && asked.isNotEmpty() }
        answerGate!!.complete(Unit)
        until("speaking the first sentence while the model writes") { s.phase.value == VoicePhase.Speaking }
        until("first sentence said") { out.spoken.size == 1 }
        replyGate!!.complete(Unit)
        until("idle") { s.phase.value == VoicePhase.Idle && timings.isNotEmpty() }
        assertEquals(listOf("what is the tallest mountain"), asked.toList())
        assertEquals(listOf("Mount Everest is the tallest.", "It is in Nepal."), out.spoken.toList())
        assertEquals(listOf("capture", "release", "reply", "release"), focus.toList())
        val t = timings.single()
        assertEquals(60L, t.sttMs)
        assertEquals(500L, t.silenceWaitMs)
        assertEquals(400L, t.firstTextMs)
        assertEquals(460L, t.toFirstWordMs)
        assertTrue(problems.isEmpty())
    }

    @Test fun theVoiceTurnTimesTheEndOfSpeechToTheFirstWord() {
        out.synthMs = 250
        val s = session()
        s.tap()
        until("timed") { timings.size == 1 }
        // Listening ends at +100 (end of speech); speech to text 60, the
        // model's first words 400, the voice's first audio 250 after that.
        assertEquals(710L, timings.single().toFirstWordMs)
        assertEquals(VoicePhase.Idle, s.phase.value)
    }

    @Test fun aTapWhileListeningSendsWhatWasHeard() {
        heardGate = CompletableDeferred()
        val s = session()
        s.tap()
        until("hearing speech") { s.phase.value == VoicePhase.Listening && s.heardSpeech.value }
        s.tap()   // done speaking
        until("answered") { asked.size == 1 && s.phase.value == VoicePhase.Idle && timings.size == 1 }
        assertEquals(1, ended)
    }

    @Test fun aTapBeforeAnySpeechEndsTheTurnWithNothingSent() {
        val gate = CompletableDeferred<Unit>()
        val s = VoiceSession(
            scope = scope, blocker = { null },
            listen = { _ -> gate.await(); null },
            endListening = { ended++; gate.complete(Unit) },
            transcribe = { "x" }, answer = { _, _ -> { _ -> } }, voice = { out }, stopModel = { modelStops++ },
        )
        s.tap()
        until("listening") { s.phase.value == VoicePhase.Listening }
        assertFalse(s.heardSpeech.value)
        s.tap()   // stop: nothing was said yet
        until("idle") { s.phase.value == VoicePhase.Idle }
        assertEquals(1, ended)
        assertTrue(asked.isEmpty())
    }

    @Test fun stopWhileListeningClosesTheMicAndSendsNothing() {
        heardGate = CompletableDeferred()
        val s = session()
        s.tap()
        until("listening") { s.phase.value == VoicePhase.Listening && s.heardSpeech.value }
        s.stop()
        assertEquals(VoicePhase.Idle, s.phase.value)
        until("wound down") { focus.lastOrNull() == "release" }
        Thread.sleep(50)
        assertTrue(asked.isEmpty())
        assertEquals(1, ended)
    }

    @Test fun stopWhileTranscribingSendsNothing() {
        transcribeGate = CompletableDeferred()
        val s = session()
        s.tap()
        until("transcribing") { s.phase.value == VoicePhase.Transcribing }
        s.tap()
        assertEquals(VoicePhase.Idle, s.phase.value)
        Thread.sleep(50)
        assertTrue(asked.isEmpty())
        assertNull(s.lastStopMs)
    }

    @Test fun stopWhileThinkingStopsTheModel() {
        replyGate = CompletableDeferred()
        replyDeltas = listOf("no full stop yet", " still none")
        val s = session()
        s.tap()
        until("thinking") { s.phase.value == VoicePhase.Thinking && asked.isNotEmpty() }
        s.tap()
        assertEquals(VoicePhase.Idle, s.phase.value)
        assertEquals(1, modelStops)
        assertNull("not speaking: no stop time", s.lastStopMs)
        Thread.sleep(50)
        assertTrue(timings.isEmpty())
    }

    @Test fun stopWhileSpeakingSilencesItAtOnce() {
        out.gate = CompletableDeferred()   // the voice holds its first sentence
        replyGate = CompletableDeferred()
        replyDeltas = listOf("A first sentence. ", "never said. ")
        val s = session()
        s.tap()
        until("speaking") { s.phase.value == VoicePhase.Speaking }
        s.tap()
        assertEquals(VoicePhase.Idle, s.phase.value)
        assertEquals(1, out.stops)
        assertEquals(1, modelStops)
        assertNotNull(s.lastStopMs)
        until("wound down") { focus.lastOrNull() == "release" }
        Thread.sleep(50)
        assertTrue(timings.isEmpty())   // a stopped turn files no timing
        assertTrue(out.spoken.isEmpty())
    }

    @Test fun aBlockedTurnSaysWhyAndNeverOpensTheMic() {
        blocker = "the microphone permission is needed to talk to Domovoi on this phone"
        val s = session()
        s.tap()
        until("the reason") { problems.isNotEmpty() }
        assertEquals("the microphone permission is needed to talk to Domovoi on this phone", problems.single())
        assertEquals(VoicePhase.Idle, s.phase.value)
        assertTrue(focus.isEmpty())
    }

    @Test fun theMicInUseElsewhereIsAProblemNotACrash() {
        micFails = IOException("The microphone is in use by the chat mic.")
        val s = session()
        s.tap()
        until("the reason") { problems.isNotEmpty() }
        assertEquals("The microphone is in use by the chat mic.", problems.single())
        until("idle") { s.phase.value == VoicePhase.Idle && focus.lastOrNull() == "release" }
    }

    @Test fun nothingHeardOrNothingRecognisedAsksAgain() {
        utterance = null
        val s = session()
        s.tap()
        until("nothing heard") { problems.size == 1 && s.phase.value == VoicePhase.Idle }
        assertTrue(problems[0].startsWith("nothing was heard"))
        utterance = Utterance(FloatArray(8_000), 0, 0, EndedBy.SILENCE)
        transcript = " . "
        Thread.sleep(20)
        s.tap()
        until("nothing recognised") { problems.size == 2 }
        assertTrue(problems[1].startsWith("didn't catch that"))
        assertTrue(asked.isEmpty())
    }

    @Test fun anEngineFailureEndsTheTurn() {
        replyFails = IllegalStateException("no OpenCL")
        val s = session()
        s.tap()
        until("the problem") { problems.isNotEmpty() }
        assertEquals("the voice turn failed: no OpenCL", problems.single())
        until("idle") { s.phase.value == VoicePhase.Idle }
    }

    @Test fun aVoiceThatWillNotStartStillGetsTheWrittenAnswer() {
        voiceFails = IllegalStateException("this phone has no working text-to-speech engine")
        val s = session()
        s.tap()
        until("answered") { asked.size == 1 && s.phase.value == VoicePhase.Idle && timings.size == 1 }
        assertTrue(problems.single().startsWith("the voice could not start"))
        assertNull(timings.single().toFirstWordMs)
    }

    @Test fun aTypedReplyIsReadAloudThroughAnswerAloud() {
        out.gate = CompletableDeferred()
        val s = session()
        s.answerAloud { onDelta ->
            onDelta("Typed question's answer. ")
            onDelta("Second part.")
        }
        until("speaking") { s.phase.value == VoicePhase.Speaking }
        assertTrue("no mic for a typed turn", focus.none { it == "capture" })
        out.gate!!.complete(Unit)
        until("spoken") { out.spoken.isNotEmpty() && s.phase.value == VoicePhase.Idle }
        // The whole reply was written before the voice started: one go.
        assertEquals(listOf("Typed question's answer. Second part."), out.spoken.toList())
        assertTrue(asked.isEmpty())
    }
}

/** How a streamed reply reaches the voice (SpokenReply). */
class SpokenReplyTest {
    private val clock = TestClock()

    @Test fun theFirstSentenceGoesAloneAndLaterOnesTogether() = kotlinx.coroutines.runBlocking {
        val out = GatedSpeechOut(clock).apply { gate = CompletableDeferred() }
        var speaking = 0
        val reply = SpokenReply(out, clock.read) { speaking++ }
        val speaker = launch(Dispatchers.Default) { reply.run() }
        reply.push("Here is the **first** one")
        reply.push(". And then ")
        while (out.started.isEmpty()) delay(1)
        assertEquals(listOf("Here is the first one."), out.started.toList())   // Markdown stripped
        reply.push("a second. A [third](http://x) one.")
        reply.finish()
        out.gate!!.complete(Unit)
        speaker.join()
        assertEquals(listOf("Here is the first one.", "And then a second. A third one."), out.spoken.toList())
        assertEquals(1, speaking)
        assertNotNull(reply.firstAudioNs)
    }

    @Test fun stopSilencesItAndDropsTheRest() = kotlinx.coroutines.runBlocking {
        val out = GatedSpeechOut(clock).apply { gate = CompletableDeferred() }
        val reply = SpokenReply(out, clock.read) {}
        val speaker = launch(Dispatchers.Default) { runCatching { reply.run() } }
        reply.push("One sentence. ")
        while (out.started.isEmpty()) delay(1)
        reply.push("Another sentence. ")
        reply.stop()
        speaker.join()
        assertEquals(1, out.stops)
        assertTrue(out.spoken.isEmpty())
        assertEquals(1, out.started.size)
    }
}

/** A voice for the session tests: speaks after [synthMs]; with a [gate] it
 *  holds each sentence until the gate opens ([stop] cancels it). */
class GatedSpeechOut(private val clock: TestClock, var synthMs: Long = 0) : SpeechOut {
    val spoken = CopyOnWriteArrayList<String>()
    val started = CopyOnWriteArrayList<String>()
    @Volatile var stops = 0
    @Volatile var gate: CompletableDeferred<Unit>? = null

    override suspend fun speak(sentence: String): SpokenSentence {
        this.started += sentence
        val startedNs = clock.nowNs
        clock.advanceMs(synthMs)
        val first = clock.nowNs
        gate?.await()
        spoken += sentence
        delay(1)
        return SpokenSentence(sentence, startedNs, first, clock.nowNs, 1.0)
    }

    override fun stop() {
        stops++
        gate?.cancel()
    }

    override fun close() {}
}
