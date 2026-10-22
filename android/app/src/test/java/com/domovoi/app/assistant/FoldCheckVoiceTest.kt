package com.domovoi.app.assistant

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Kamron's Fold check (2026-10-21), the voice and card findings, through the
 * real controller and voice session:
 * 1. a card's button tapped twice set two Clock timers;
 * 2. spoken turns that reached the model came back "stopped" with no answer;
 * 3. a spoken "yes" to an open timer card didn't run it.
 */
class FoldCheckVoiceTest {
    private val root: File = Files.createTempDirectory("fold-voice").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val settings = MemoryAssistantSettings()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val said = CopyOnWriteArrayList<String>()
    private val clock = TestClock()
    private val engines = CopyOnWriteArrayList<ScriptedEngine>()
    private val host = FakeActionHost()
    private val la = ZoneId.of("America/Los_Angeles")
    private val nowMs = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la).toInstant().toEpochMilli()

    private fun model(id: String, kind: ModelKind) = CatalogModel(
        id, id, kind, listOf(ModelFile("$id.bin", "https://example.org/$id.bin", 10, "0".repeat(64))),
        "MIT", "https://example.org/license",
    )

    private val llm = model("test-llm", ModelKind.LLM)
    private val stt = model("test-stt", ModelKind.STT)
    private val vad = model("test-vad", ModelKind.VAD)

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private val quiet = ShortArray(MicCapture.CHUNK_SAMPLES)
    private val loud = ShortArray(MicCapture.CHUNK_SAMPLES) { 16_384 }

    /** A mic that hears one utterance per listen, a recognizer that hears
     *  [heard] in order (the last one again once they run out), a voice. */
    private inner class Kit(vararg heard: String, val out: SpeechOut = GatedSpeechOut(clock)) : VoiceKit {
        val queue = ConcurrentLinkedQueue(heard.toList())
        @Volatile var last = heard.lastOrNull().orEmpty()
        override val mic = MicCapture(source = { FakeAudioSource(listOf(quiet, loud, loud, quiet, quiet, quiet)) }, clock = clock.read)
        override suspend fun detector(): SpeechDetector = FakeSpeechDetector(silenceChunks = 3)
        override suspend fun recognizer(): SpeechToText = FakeSpeechToText(clock, queue.poll() ?: last, ms = 20)
        override suspend fun speech(): SpeechOut = out
        override val voiceName: String = "Kristin"
        override fun close() {}
    }

    private fun install(vararg ms: CatalogModel) = ms.forEach { m ->
        m.files.forEach { f -> store.fileFor(m, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(m)
    }

    private fun controller(kit: VoiceKit, engine: () -> ScriptedEngine = { ScriptedEngine() }): AssistantController {
        install(llm, stt, vad)
        return built(kit, engine)
    }

    private fun built(kit: VoiceKit, engine: () -> ScriptedEngine) = AssistantController(
        memory = PhoneMemory((7.6 * (1L shl 30)).toLong(), lowRamDevice = false),
        store = store,
        conversations = conversations,
        jobs = FakeJobs(),
        settings = settings,
        newEngine = { engine().also { engines += it } },
        scope = scope,
        models = listOf(llm, stt, vad),
        voiceKit = kit,
        nanos = clock.read,
        clock = { nowMs },
        clockStyle = { ClockStyle(la, Locale.US, is24Hour = false) },
        actions = host,
    ).also { c ->
        scope.launch { c.messages.collect { said += it } }
        until("ready") { c.state.value.installed && c.state.value.voiceReady }
    }

    private fun until(what: String, timeoutMs: Long = 8_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what (said $said)")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ui() = state.value

    // ── 1. Two taps, one timer ──────────────────────────────────────────

    /** The real-device path: the card came from a spoken turn, and was
     *  tapped while its question was still being read aloud. When the voice
     *  finished, the turn's timing was written back with that turn's copy of
     *  the message, whose card was still OPEN: the card reopened, and the
     *  second tap set a second timer. */
    @Test fun aCardTappedWhileItsQuestionIsSpokenStaysDoneAndASecondTapSetsNothing() {
        val out = GatedSpeechOut(clock).apply { gate = CompletableDeferred() }
        val kit = Kit("Set a timer for 2 minutes called pasta", out = out)
        val c = controller(kit)
        c.talk()
        until("the card") { c.ui().conversation.messages.lastOrNull()?.card?.state == CardState.OPEN && !c.ui().replying }
        until("speaking") { c.ui().voicePhase == VoicePhase.Speaking }
        val id = c.ui().conversation.id
        val index = c.ui().conversation.messages.lastIndex
        c.actionConfirmed(id, index)
        until("the tap ran") { c.ui().conversation.messages[index].card?.state == CardState.DONE }
        out.gate!!.complete(Unit)
        until("the turn ended and its timing was filed") {
            c.ui().voicePhase == VoicePhase.Idle && c.ui().conversation.messages[index].voiceMs != null
        }
        until("the disk has the timing") { conversations.load(id)?.messages?.get(index)?.voiceMs != null }
        assertEquals("still done on screen", CardState.DONE, c.ui().conversation.messages[index].card?.state)
        assertEquals("still done on disk", CardState.DONE, conversations.load(id)!!.messages[index].card?.state)
        c.actionConfirmed(id, index)
        Thread.sleep(300)
        assertEquals("one tap, one timer", 1, host.sent.size)
    }

    /** A card the disk already has answered (any copy saying it isn't
     *  open) runs nothing, even while the screen's copy still shows it open. */
    @Test fun aTapOnACardTheDiskHasAnsweredRunsNothing() {
        val kit = Kit("Set a timer for 2 minutes")
        val c = controller(kit)
        c.send("Set a timer for 2 minutes")
        until("the card") { !c.ui().replying && c.ui().conversation.messages.lastOrNull()?.card?.state == CardState.OPEN }
        val id = c.ui().conversation.id
        val index = c.ui().conversation.messages.lastIndex
        val disk = conversations.load(id)!!
        conversations.save(disk.copy(messages = disk.messages.toMutableList().also {
            it[index] = it[index].copy(card = it[index].card!!.copy(state = CardState.DONE))
        }))
        c.actionConfirmed(id, index)
        Thread.sleep(300)
        assertTrue("nothing sent: ${host.sent}", host.sent.isEmpty())
    }

    // ── 2. A spoken turn is never stopped by the app ────────────────────

    /** LiteRT-LM reports its own CANCELLED as a CancellationException; the
     *  reply caught it as a stop and showed "stopped". Nobody tapped stop:
     *  it is asked once more, on a fresh conversation, and answered. */
    @Test fun aRuntimeCancelNobodyAskedForIsAskedAgainNotShownAsStopped() {
        val kit = Kit("tell me a fun fact about octopuses")
        val c = controller(kit, engine = {
            ScriptedEngine().apply { failNext = java.util.concurrent.CancellationException("CANCELLED: Task cancelled") }
        })
        c.talk()
        until("answered") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying && c.ui().conversation.messages.size >= 2 }
        val a = c.ui().conversation.messages.last()
        assertFalse("not stopped", a.stopped)
        assertNull(a.error)
        assertEquals("Hello there.", a.text)
    }

    /** A voice that fails part-way (a TTS error) used to cancel the model's
     *  reply through the turn's shared scope: the reply showed "stopped". */
    @Test fun aFailingVoiceNeverStopsTheWrittenReply() {
        val broken = object : SpeechOut {
            override suspend fun speak(sentence: String): SpokenSentence = error("text to speech failed (synthesis)")
            override fun stop() {}
            override fun close() {}
        }
        val kit = Kit("why is the sky blue", out = broken)
        val gate = CompletableDeferred<Unit>()
        val c = controller(kit, engine = {
            // The first sentence is spoken (and fails) while the model is
            // still writing the rest.
            ScriptedEngine().apply { this.gate = gate; script = listOf("Light scatters. ", "Blue scatters most.") }
        })
        c.talk()
        until("the voice failed") { said.isNotEmpty() }
        gate.complete(Unit)
        until("answered") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying && c.ui().conversation.messages.size >= 2 }
        val a = c.ui().conversation.messages.last()
        assertFalse("not stopped", a.stopped)
        assertEquals("Light scatters. Blue scatters most.", a.text)
        until("said in words") { said.any { it.startsWith("the voice stopped") } }
    }

    /** The person's own stop is still a stop. */
    @Test fun thePersonsStopIsStillAStop() {
        val kit = Kit("why is the sky blue")
        val c = controller(kit, engine = { ScriptedEngine().apply { gate = CompletableDeferred(); script = listOf("Light ", "scatters.") } })
        c.talk()
        until("writing") { c.ui().conversation.messages.lastOrNull()?.text == "Light " }
        c.stop()
        until("idle") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying }
        assertTrue(c.ui().conversation.messages.last().stopped)
    }

    // ── 3. A spoken yes to the card ─────────────────────────────────────

    /** The card's question is read aloud ("… Say yes or no."); the mic then
     *  opens by itself for the answer, and "yes" sets the timer. */
    @Test fun afterASpokenCardTheMicListensForTheYesAndItRuns() {
        val kit = Kit("Set a timer for 1 minute", "Yes.")
        val c = controller(kit)
        c.talk()
        until("the timer was sent") { host.sent.isNotEmpty() }
        until("idle") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying }
        assertEquals(listOf(ClockRequest(ClockIntent.SET_TIMER, seconds = 60, label = "Timer · 3:42 PM")), host.sent.toList())
        val ms = c.ui().conversation.messages
        assertEquals(CardState.DONE, ms[1].card?.state)
        assertEquals("Yes.", ms[2].text)
        assertTrue(ms[2].spoken)
    }

    /** Fold re-check (2026-10-22): a spoken "No." to a second card in the
     *  chat, right after a voice turn, set it cancelled but the reply was the
     *  model's. A yes or no to a card always gets the card's own reply. */
    @Test fun aSpokenNoToASecondCardIsTheCardsOwnReply() {
        val kit = Kit("Set a timer for 2 minutes", "Yes.", "Set a timer for 2 minutes", "No.")
        val c = controller(kit)
        c.talk()
        until("the first timer") { host.sent.size == 1 }
        until("idle") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying }
        c.talk()
        until("answered no") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying && c.ui().conversation.messages.size >= 8 }
        val ms = c.ui().conversation.messages
        assertEquals("No.", ms[6].text)
        assertEquals(ActionTexts.DECLINED, ms[7].text)
        assertEquals(CardState.DISMISSED, ms[5].card?.state)
        assertEquals(1, host.sent.size)
        assertTrue("no model reply", engines.all { it.replies.isEmpty() })
    }

    /** A yes or no to a card that's already answered (a tap got there
     *  first) is still the card's reply, never the model's. */
    @Test fun aYesOrNoToAnAnsweredCardIsNeverTheModels() {
        val kit = Kit("x")
        val c = controller(kit)
        c.send("Set a timer for 2 minutes")
        until("the card") { !c.ui().replying && c.ui().conversation.messages.lastOrNull()?.card?.state == CardState.OPEN }
        c.actionDeclined(c.ui().conversation.id, 1)
        until("cancelled") { c.ui().conversation.messages[1].card?.state == CardState.DISMISSED }
        c.send("No.")
        until("reply") { !c.ui().replying && c.ui().conversation.messages.size >= 4 }
        assertEquals(ActionTexts.DECLINED, c.ui().conversation.messages[3].text)
        // A yes to a card that was cancelled: answered already, still no model.
        c.send("Set a timer for 3 minutes")
        until("card 2") { !c.ui().replying && c.ui().conversation.messages.size >= 6 }
        c.actionDeclined(c.ui().conversation.id, 5)
        until("cancelled 2") { c.ui().conversation.messages[5].card?.state == CardState.DISMISSED }
        c.send("yes")
        until("reply 2") { !c.ui().replying && c.ui().conversation.messages.size >= 8 }
        assertEquals(ActionTexts.ALREADY_ANSWERED, c.ui().conversation.messages[7].text)
        assertTrue("no model reply", engines.all { it.replies.isEmpty() })
        assertTrue(host.sent.isEmpty())
    }

    /** Review round 1: noise in the window (nothing a recognizer can read)
     *  closes it quietly too, with no "didn't catch that". */
    @Test fun noiseInTheAnswerWindowClosesItQuietly() {
        val kit = Kit("Set a timer for 1 minute", "  ")
        val c = controller(kit)
        c.talk()
        until("the card") { c.ui().conversation.messages.lastOrNull()?.card != null }
        until("idle") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying && kit.queue.isEmpty() }
        Thread.sleep(150)
        assertEquals(CardState.OPEN, c.ui().conversation.messages.last().card?.state)
        assertFalse(said.toString(), said.any { it.startsWith("didn't catch that") || it.startsWith("nothing was heard") })
        assertFalse(c.ui().voiceAnswering)
    }

    /** Only an open action card opens the mic by itself; a lookup card
     *  waits for a tap (review round 1: internet off by default). */
    @Test fun onlyAnActionCardOpensTheAnswerMic() {
        val action = StoredMessage(Speaker.DOMOVOI, "Set a timer?", 0, card = PhoneCard(CardKind.ACTION, action = PhoneAction(ActionKind.SET_TIMER, seconds = 60)))
        assertTrue(opensAnswerMic(action))
        assertFalse(opensAnswerMic(action.copy(card = action.card!!.copy(state = CardState.DONE))))
        val lookup = StoredMessage(Speaker.DOMOVOI, "Look this up?", 0, lookupCard = com.domovoi.app.assistant.lookup.LookupCard(query = "outkast"))
        assertFalse(opensAnswerMic(lookup))
        assertEquals("listening" to "say yes or no",
            com.domovoi.app.ui.screens.assistant.voiceLine(VoicePhase.Listening, heardSpeech = false, answering = true))
    }

    /** Nobody answers: the window closes quietly, the card stays open, and
     *  no "nothing was heard" is said. */
    @Test fun anUnansweredCardWindowClosesQuietly() {
        val kit = object : VoiceKit {
            var listens = 0
            override val mic = MicCapture(source = {
                listens++
                if (listens == 1) FakeAudioSource(listOf(quiet, loud, loud, quiet, quiet, quiet)) else FakeAudioSource(emptyList())
            }, clock = clock.read)
            override suspend fun detector(): SpeechDetector = FakeSpeechDetector(silenceChunks = 3)
            override suspend fun recognizer(): SpeechToText = FakeSpeechToText(clock, "set an alarm for 7 am", ms = 20)
            override suspend fun speech(): SpeechOut = GatedSpeechOut(clock)
            override val voiceName: String = "Kristin"
            override fun close() {}
        }
        val c = controller(kit)
        c.talk()
        until("the card") { c.ui().conversation.messages.lastOrNull()?.card != null }
        until("idle") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying && kit.listens >= 2 }
        assertEquals(CardState.OPEN, c.ui().conversation.messages.last().card?.state)
        assertTrue(host.sent.isEmpty())
        Thread.sleep(100)
        assertFalse(said.toString(), said.any { it.startsWith("nothing was heard") })
    }
}
