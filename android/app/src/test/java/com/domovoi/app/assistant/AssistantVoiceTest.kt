package com.domovoi.app.assistant

import com.domovoi.app.audio.MicArbiter
import com.domovoi.app.audio.MicOwnership
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.time.ZoneId
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Voice on the assistant (M2): the spoken turn through the controller (the
 * transcript in the conversation, the reply read aloud, its timing filed),
 * plan decision 7 (typed replies silent unless turned on), the speech files
 * for a phone set up for text only, and the one-mic-owner rule.
 */
class AssistantVoiceTest {
    private val root: File = Files.createTempDirectory("assistant-voice").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val settings = MemoryAssistantSettings()
    private val jobs = FakeJobs()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val said = CopyOnWriteArrayList<String>()
    private val clock = TestClock()
    private val engines = CopyOnWriteArrayList<ScriptedEngine>()

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

    private fun install(vararg ms: CatalogModel) = ms.forEach { m ->
        m.files.forEach { f -> store.fileFor(m, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(m)
    }

    private val quiet = ShortArray(MicCapture.CHUNK_SAMPLES)
    private val loud = ShortArray(MicCapture.CHUNK_SAMPLES) { 16_384 }

    /** A kit of fakes: a mic that says something then stops, a recognizer
     *  that hears [heard], and a voice that records what it says. */
    private inner class FakeKit(var heard: String = "why is the sky blue") : VoiceKit {
        val out = GatedSpeechOut(clock)
        @Volatile var source: () -> AudioSource = { FakeAudioSource(listOf(quiet, loud, loud, quiet, quiet, quiet)) }
        @Volatile var closes = 0
        override val mic = MicCapture(source = { source() }, clock = clock.read)
        override suspend fun detector(): SpeechDetector = FakeSpeechDetector(silenceChunks = 3)
        override suspend fun recognizer(): SpeechToText = FakeSpeechToText(clock, heard, ms = 50)
        override suspend fun speech(): SpeechOut = out
        override val voiceName: String = "Kristin"
        override fun close() {
            closes++
        }
    }

    private fun controller(
        kit: VoiceKit,
        models: List<CatalogModel> = listOf(llm, stt, vad),
        micAllowed: () -> Boolean = { true },
        engineDeltas: List<String> = listOf("Hello", " there."),
    ) = AssistantController(
        memory = PhoneMemory((7.6 * (1L shl 30)).toLong(), lowRamDevice = false),
        store = store,
        conversations = conversations,
        jobs = jobs,
        settings = settings,
        newEngine = { ScriptedEngine().also { it.script = engineDeltas; engines += it } },
        scope = scope,
        models = models,
        voiceKit = kit,
        micAllowed = micAllowed,
        micRefusal = { MicArbiter.refusal(MicOwnership.Owner.ASSISTANT) },
        nanos = clock.read,
        clock = { EPOCH_MS },
        clockStyle = { ClockStyle(ZoneId.of("America/Los_Angeles"), Locale.US, is24Hour = false) },
    ).also { c -> scope.launch { c.messages.collect { said += it } } }

    private fun until(what: String, timeoutMs: Long = 5_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what (said $said)")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ui() = state.value

    @Test fun aSpokenQuestionIsAnsweredAloudAndKeptAsText() {
        install(llm, stt, vad)
        val kit = FakeKit()
        val c = controller(kit)
        until("ready") { c.ui().installed && c.ui().voiceReady }
        c.talk()
        until("answered") { c.ui().conversation.messages.size == 2 && c.ui().voicePhase == VoicePhase.Idle }
        val (q, a) = c.ui().conversation.messages
        assertEquals("why is the sky blue", q.text)
        assertTrue(q.spoken)
        assertEquals("Hello there.", a.text)
        assertEquals(listOf("Hello there."), kit.out.spoken.toList())
        until("timing filed") { c.ui().conversation.messages.last().voiceMs != null }
        val timed = c.ui().conversation.messages.last()
        assertEquals(50L, timed.sttMs)
        assertEquals(500L, timed.silenceWaitMs)
        until("saved with its timing") { conversations.load(c.ui().conversation.id)?.messages?.last()?.voiceMs != null }
        assertNull("the mic is given back", MicArbiter.shared.holder())
    }

    /** The owner's report (2026-10-16): "What time is it?" by voice got "I
     *  cannot tell you the time". The phone's clock answers it, aloud, at
     *  once: no model is loaded for it. */
    @Test fun aSpokenTimeQuestionIsAnsweredFromThePhoneClockAloud() {
        install(llm, stt, vad)
        val kit = FakeKit(heard = "What time is it?")
        val c = controller(kit)
        until("ready") { c.ui().installed && c.ui().voiceReady }
        c.talk()
        until("answered") { c.ui().conversation.messages.size == 2 && c.ui().voicePhase == VoicePhase.Idle }
        val (q, a) = c.ui().conversation.messages
        assertEquals("What time is it?", q.text)
        assertTrue(q.spoken)
        assertEquals("It's 3:42 PM.", a.text)
        assertNull(a.error)
        assertEquals(listOf("It's 3:42 PM."), kit.out.spoken.toList())
        assertTrue("no model for the clock", engines.isEmpty())
        until("timing filed") { c.ui().conversation.messages.last().voiceMs != null }
        until("saved") { conversations.load(c.ui().conversation.id)?.messages?.last()?.text == "It's 3:42 PM." }
    }

    @Test fun typedRepliesStaySilentUnlessTurnedOn() {
        install(llm, stt, vad)
        val kit = FakeKit()
        val c = controller(kit)
        until("ready") { c.ui().installed && c.ui().voiceReady }
        assertFalse(c.ui().speakTyped)   // plan decision 7
        c.send("typed question")
        until("answered") { c.ui().conversation.messages.size == 2 && !c.ui().replying }
        Thread.sleep(50)
        assertTrue(kit.out.spoken.isEmpty())
        assertFalse(c.ui().conversation.messages.first().spoken)

        c.setSpeakTyped(true)
        assertTrue(settings.speakTyped)
        c.send("another typed question")
        until("answered aloud") { kit.out.spoken.size == 1 && c.ui().voicePhase == VoicePhase.Idle }
        assertEquals(4, c.ui().conversation.messages.size)
    }

    @Test fun theMicHeldByAnotherOwnerIsRefusedInWords() {
        install(llm, stt, vad)
        val c = controller(FakeKit())
        until("ready") { c.ui().installed && c.ui().voiceReady }
        val other = MicArbiter.tryAcquire(MicOwnership.Owner.CHAT_MIC)!!
        try {
            c.talk()
            until("the reason") { said.any { it.contains("in use by the chat mic") } }
            assertEquals(VoicePhase.Idle, c.ui().voicePhase)
            assertEquals(MicOwnership.Owner.CHAT_MIC, MicArbiter.shared.holder())
        } finally {
            other.close()
        }
        assertTrue(c.ui().conversation.messages.isEmpty())
    }

    @Test fun noMicPermissionIsSaidAndNothingOpens() {
        install(llm, stt, vad)
        val c = controller(FakeKit(), micAllowed = { false })
        until("ready") { c.ui().installed && c.ui().voiceReady }
        c.talk()
        until("the reason") { said.any { it.startsWith("the microphone permission is needed") } }
        assertNull(MicArbiter.shared.holder())
    }

    @Test fun aPhoneSetUpForTextOnlyIsOfferedJustTheSpeechFiles() {
        install(llm)
        val c = controller(FakeKit())
        until("text ready, voice not") { c.ui().installed && c.ui().voiceSetup is ModelSetup.Missing }
        assertEquals(20L, c.ui().voiceBytes)
        c.talk()
        until("the reason") { said.any { it == "the speech files aren't on the phone yet" } }
        c.downloadVoice()
        assertEquals(listOf(listOf("test-stt", "test-vad") to false), jobs.starts.toList())
        until("downloading") { c.ui().voiceSetup is ModelSetup.Downloading }
        install(stt, vad)
        jobs.flow.value = DownloadJob("job-1", androidx.work.WorkInfo.State.SUCCEEDED, 0, false, setOf("test-stt", "test-vad"), null, null)
        until("voice ready") { c.ui().voiceReady }
    }

    @Test fun theFirstSetupDownloadsTheModelAndTheSpeechFilesTogether() {
        val c = controller(FakeKit())
        until("missing") { c.ui().setup is ModelSetup.Missing }
        assertEquals(30L, c.ui().totalBytes)
        c.download()
        assertEquals(listOf(listOf("test-llm", "test-stt", "test-vad") to false), jobs.starts.toList())
    }

    @Test fun leavingTheScreenClosesTheMic() {
        install(llm, stt, vad)
        val kit = FakeKit()
        // A mic that hears nothing, slowly: the turn stays listening.
        kit.source = {
            object : AudioSource {
                override fun start() {}
                override fun read(buffer: ShortArray): Int {
                    Thread.sleep(20)
                    buffer.fill(0)
                    return buffer.size
                }
                override fun stop() {}
            }
        }
        val c = controller(kit)
        until("ready") { c.ui().installed && c.ui().voiceReady }
        c.screenShown()
        c.talk()
        until("listening") { c.ui().voicePhase == VoicePhase.Listening && MicArbiter.shared.holder() != null }
        c.screenHidden()
        until("closed") { c.ui().voicePhase == VoicePhase.Idle && MicArbiter.shared.holder() == null }
        assertTrue(c.ui().conversation.messages.isEmpty())
    }

    @Test fun stopWhileSpeakingSilencesTheVoiceAndKeepsTheWords() {
        install(llm, stt, vad)
        val kit = FakeKit()
        kit.out.gate = kotlinx.coroutines.CompletableDeferred()
        val c = controller(kit, engineDeltas = listOf("First sentence. ", "Second sentence."))
        until("ready") { c.ui().installed && c.ui().voiceReady }
        c.talk()
        until("speaking") { c.ui().voicePhase == VoicePhase.Speaking }
        c.stop()
        until("idle") { c.ui().voicePhase == VoicePhase.Idle && !c.ui().replying }
        assertEquals(1, kit.out.stops)
        assertNotNull(c.ui().lastStopMs)
        assertTrue(c.ui().conversation.messages.last().text.isNotEmpty())
    }
}

/** 2026-10-15 15:42:00 in Los Angeles (PDT, UTC-07:00). */
private val EPOCH_MS = java.time.ZonedDateTime.of(2026, 10, 15, 15, 42, 0, 0, ZoneId.of("America/Los_Angeles"))
    .toInstant().toEpochMilli()

/** MicCapture takes the mic through the app's one owner rule. */
class MicCaptureOwnerTest {
    private val clock = TestClock()

    @Test fun anotherOwnersMicIsRefusedAndOursIsGivenBack() = runBlocking {
        val loud = ShortArray(MicCapture.CHUNK_SAMPLES) { 16_384 }
        val quiet = ShortArray(MicCapture.CHUNK_SAMPLES)
        val held = MicArbiter.tryAcquire(MicOwnership.Owner.DROP_IN)!!
        try {
            val source = FakeAudioSource(listOf(loud))
            val refused = runCatching { MicCapture({ source }, clock.read).listen(FakeSpeechDetector()) }.exceptionOrNull()
            assertTrue("$refused", refused is IOException && refused.message == "The microphone is in use by a drop-in call.")
            assertFalse("the mic was never opened", source.started)
        } finally {
            held.close()
        }
        val source = FakeAudioSource(listOf(loud, quiet, quiet, quiet))
        assertNotNull(MicCapture({ source }, clock.read).listen(FakeSpeechDetector(silenceChunks = 3)))
        assertNull(MicArbiter.shared.holder())
    }
}
