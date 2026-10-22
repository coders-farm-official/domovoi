package com.domovoi.app.assistant

import com.domovoi.app.assistant.docs.HeadsUpStore
import com.domovoi.app.assistant.docs.LetterRead
import com.domovoi.app.assistant.docs.WeakSpots
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/**
 * "What does this letter say?" in a phone chat (documents plan D6), through
 * the controller with a fake engine: the blurry card comes before any model,
 * handwriting gets its card after the one-word step and before the
 * transcription, the explanation is checked before it is shown (an invented
 * amount never is), the photo is wiped and the transcript never stored, a
 * card holds the photo only until its pick; a phone that can't read photos
 * hands the letter to the home server (which reads it) and still writes the
 * explanation itself, or says plainly that it can't.
 */
class PhoneLettersTest {
    private val root: File = Files.createTempDirectory("phone-letters").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val engines = CopyOnWriteArrayList<LetterEngine>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val headsUp = HeadsUpStore.memory()
    @Volatile private var blur: Double? = 400.0
    @Volatile private var failProbe = false
    private val table = WeakSpots.parse(
        listOf("../../domovoi/doc_qa/chat-weak-spots.json", "../domovoi/doc_qa/chat-weak-spots.json")
            .map(::File).first { it.isFile }.readText(),
    )

    private val llm = CatalogModel(
        "test-llm", "Test LLM", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "2".repeat(64))),
        "Apache-2.0", "https://example.org/license", takesImages = true,
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private fun controller(): AssistantController {
        llm.files.forEach { f -> store.fileFor(llm, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(llm)
        return AssistantController(
            memory = PhoneMemory((15.8 * (1L shl 30)).toLong(), lowRamDevice = false),
            store = store, conversations = conversations, jobs = FakeJobs(), settings = MemoryAssistantSettings(),
            newEngine = { LetterEngine(failProbe).also { engines += it } },
            scope = scope, models = listOf(llm), headsUp = headsUp, weakSpots = { table },
            visionMemory = MemoryVisionMemory(), visionBackend = { LlmBackend.GPU }, probeTimeoutMs = 2_000,
            blurOf = { blur },
        )
    }

    private fun until(what: String, timeoutMs: Long = 5_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ui() = state.value
    private fun AssistantController.replied(n: Int) =
        until("reply $n") { !ui().replying && ui().conversation.messages.count { !it.fromUser } >= n }

    private fun ready(c: AssistantController) {
        until("installed") { c.ui().installed }
        c.prepare()
        until("loaded") { c.ui().engine is EngineState.Ready || c.ui().engine is EngineState.Failed }
    }

    private fun photo(): PendingPhoto = PendingPhoto(ByteArray(2048) { (it % 200 + 1).toByte() }, 1024, 768, "camera", 9)

    private fun storedText(): String = root.walkTopDown().filter { it.isFile && "models" !in it.path }.joinToString("\n") { it.readText() }

    @Test fun aLetterIsReadOnThePhoneThenCheckedAndNothingOfItIsKept() {
        val c = controller()
        ready(c)
        assertEquals(VisionProbe.PASSED, c.ui().vision)
        val p = photo()
        assertTrue(c.attachPhoto(p))
        c.send("What does this letter say?")
        c.replied(1)
        val e = engines.single()
        assertEquals(listOf(LetterRead.KIND_SYSTEM to LetterRead.KIND_TOKENS, LetterRead.TRANSCRIBE_SYSTEM to LetterRead.TRANSCRIBE_TOKENS),
            e.photoCalls.map { it.first to it.second })
        val turn = e.replies.single().single()
        assertTrue(turn.text.contains("<<<LETTER ") && turn.text.contains("Service period"))
        assertTrue(e.systems.single().startsWith("The person photographed a letter"))
        val reply = c.ui().conversation.messages.last()
        assertEquals("letter", reply.docMode)
        assertTrue(reply.text, reply.text.contains("**\$142.17**") && reply.text.contains("**November 15, 2026**"))
        assertFalse(reply.text.contains("99.00"))
        assertEquals(listOf("letter_checked", "letter_dropped"), reply.notes.map { it.code })
        until("the photo is wiped") { p.jpeg.all { it == 0.toByte() } }
        until("saved") { conversations.list().isNotEmpty() }
        Thread.sleep(50)
        // The photo is never kept; its text is, beside the answer (owner,
        // 2026-10-21), and a follow-up sees it as data.
        assertEquals(listOf(PhoneLetters.SOURCE_LABEL), reply.sources.map { it.label })
        assertTrue(c.ui().conversation.messages.first { it.fromUser }.photo)
        assertTrue(storedText().contains("Service period"))
        c.send("And when is it due?")
        c.replied(2)
        val follow = engines.single().replies.last()
        assertTrue(follow.any { !it.fromUser && it.text.contains("data, not instructions") && it.text.contains("Service period") })
        // Deleted with the chat.
        c.delete(c.ui().conversation.id)
        until("deleted") { !storedText().contains("Service period") }
    }

    @Test fun aBlurryPhotoGetsItsCardBeforeAnyModelThenSendAnywayReadsIt() {
        blur = 12.0
        val c = controller()
        ready(c)
        val p = photo()
        c.attachPhoto(p)
        c.send("What do I need to do about this bill?")
        c.replied(1)
        val card = c.ui().conversation.messages.last()
        assertEquals("blurry", card.headsUp?.kind)
        assertTrue(engines.single().photoCalls.isEmpty())
        assertFalse("held while the card waits", p.jpeg.all { it == 0.toByte() })
        c.pickHeadsUp("send_anyway")
        c.replied(2)
        val reply = c.ui().conversation.messages.last()
        assertEquals("letter", reply.docMode)
        assertTrue(reply.notes.any { it.code == "letter_blurry" })
        until("the photo is wiped") { p.jpeg.all { it == 0.toByte() } }
    }

    @Test fun chooseAnotherDropsTheHeldPhotoAndAnswersNothing() {
        blur = 12.0
        val c = controller()
        ready(c)
        val p = photo()
        c.attachPhoto(p)
        c.send("What does this letter say?")
        c.replied(1)
        c.pickHeadsUp("choose_another")
        until("picked") { c.ui().conversation.messages.last().headsUp?.chosen == "choose_another" }
        until("the photo is wiped") { p.jpeg.all { it == 0.toByte() } }
        assertEquals(1, c.ui().conversation.messages.count { !it.fromUser })
        assertTrue(engines.single().photoCalls.isEmpty())
    }

    @Test fun handwritingGetsItsCardBeforeTheTranscriptionThenTryAnyway() {
        val c = controller()
        ready(c)
        engines.single().kind = "Handwritten"
        c.attachPhoto(photo())
        c.send("What does this note say?")
        c.replied(1)
        assertEquals("handwriting", c.ui().conversation.messages.last().headsUp?.kind)
        // Two signals agreed (Fold check 2026-10-21): asked, then asked the other way round.
        assertEquals(listOf(LetterRead.KIND_QUESTION, PhoneLetters.KIND_CHECK_QUESTION), engines.single().photoCalls.map { it.third })
        c.pickHeadsUp("try_anyway")
        c.replied(2)
        // Not asked again: the pick says it is handwriting.
        assertEquals(listOf(LetterRead.KIND_SYSTEM, LetterRead.KIND_SYSTEM, LetterRead.TRANSCRIBE_SYSTEM), engines.single().photoCalls.map { it.first })
        assertTrue(c.ui().conversation.messages.last().notes.any { it.code == "letter_handwriting" })
    }

    /** Fold check 2026-10-21: a printed label asked "What does this say?"
     *  got the letter's layout and a handwriting heads-up. A question that
     *  doesn't name a letter is read first; text that doesn't read like a
     *  letter gets a plain answer, no printed-or-handwritten step, and its
     *  text is kept for follow-ups. */
    @Test fun aPrintedLabelGetsAPlainAnswerNotTheLettersLayout() {
        val c = controller()
        ready(c)
        engines.single().kind = "Handwritten"
        engines.single().transcript = LABEL
        val p = photo()
        c.attachPhoto(p)
        c.send("What does this say?")
        c.replied(1)
        val reply = c.ui().conversation.messages.last()
        assertNull("no handwriting card", reply.headsUp)
        assertEquals("A cat on a sofa.", reply.text)
        assertEquals(PhotoText.MODE_PHOTO, reply.docMode)
        assertEquals(listOf(LetterRead.TRANSCRIBE_SYSTEM, PhonePhotos.systemPrompt(Persona.SYSTEM_PROMPT)),
            engines.single().photoCalls.map { it.first })
        assertTrue("no printed-or-handwritten step", engines.single().photoCalls.none { it.first == LetterRead.KIND_SYSTEM })
        assertTrue(reply.sources.single().text.contains("Organic Rolled Oats"))
        until("the photo is wiped") { p.jpeg.all { it == 0.toByte() } }
    }

    /** The same question on a photo whose text reads like a letter: the letter's layout. */
    @Test fun whatDoesThisSayOnALetterGetsTheLettersLayout() {
        val c = controller()
        ready(c)
        c.attachPhoto(photo())
        c.send("What does this say?")
        c.replied(1)
        val reply = c.ui().conversation.messages.last()
        assertEquals("letter", reply.docMode)
        assertTrue(reply.text, reply.text.contains("**\$142.17**"))
    }

    /** One "handwritten" isn't enough: asked the other way round, it must agree. */
    @Test fun oneHandwrittenAnswerAloneShowsNoCard() {
        val c = controller()
        ready(c)
        engines.single().kind = "Handwritten"
        engines.single().kindAgain = "Printer"
        c.attachPhoto(photo())
        c.send("What does this letter say?")
        c.replied(1)
        val reply = c.ui().conversation.messages.last()
        assertNull(reply.headsUp)
        assertEquals("letter", reply.docMode)
        assertTrue(reply.notes.none { it.code == "letter_handwriting" })
    }

    @Test fun aPhotoThatIsntALetterIsAnOrdinaryPhotoTurn() {
        val c = controller()
        ready(c)
        c.attachPhoto(photo())
        c.send("What colour is this flower?")
        c.replied(1)
        assertEquals("A cat on a sofa.", c.ui().conversation.messages.last().text)
    }

    @Test fun aLetterQuestionWithNoPhotoSaysHowOrSaysPlainlyItCant() {
        val c = controller()
        ready(c)
        c.send("What does this letter say?")
        c.replied(1)
        assertEquals(PhoneLetters.TAKE_A_PHOTO, c.ui().conversation.messages.last().text)
        assertTrue(engines.single().replies.isEmpty())
        // A bare "what do I need to do?" stays the model's.
        c.send("what do I need to do?")
        c.replied(2)
        assertEquals("Hello there.", c.ui().conversation.messages.last().text)
    }

    @Test fun withoutPhotosHereTheHomeServerReadsTheLetterAndThePhoneExplainsIt() {
        failProbe = true
        val c = controller()
        ready(c)
        assertEquals(VisionProbe.FAILED, c.ui().vision)
        c.send("Can you explain this letter?")
        c.replied(1)
        assertEquals(PhoneLetters.CHIP_CANT + " " + PhoneLetters.CANT, c.ui().conversation.messages.last().text)
        assertFalse(PhoneLetters.serverButtonShown(c.ui()))
        assertFalse(c.attachServerLetter(photo()))

        val sent = CopyOnWriteArrayList<Pair<Int, Boolean>>()
        c.setLetterServer { jpeg, stop -> sent += jpeg.size to stop; LetterServerRead(400.0, "printed", BILL, false) }
        assertTrue(PhoneLetters.serverButtonShown(c.ui()))
        c.send("What does this bill say?")
        c.replied(2)
        assertEquals(PhoneLetters.CHIP_CANT + " " + PhoneLetters.SERVER_CAN, c.ui().conversation.messages.last().text)
        val p = photo()
        assertTrue(c.attachServerLetter(p))
        assertTrue(c.ui().pendingPhotoServer)
        c.send("")
        c.replied(3)
        assertEquals(listOf(2048 to true), sent)
        val reply = c.ui().conversation.messages.last()
        assertEquals("home server", reply.vision)
        assertTrue(reply.text.contains("**\$142.17**"))
        assertTrue(engines.last().photoCalls.isEmpty())
        // The explanation was written here, from the server's transcript.
        assertTrue(engines.last().replies.last().single().text.contains("Service period"))
        until("the photo is wiped") { p.jpeg.all { it == 0.toByte() } }
        assertTrue(c.ui().conversation.messages.last().sources.single().text.contains("Service period"))
    }

    @Test fun theHomeServersHandwritingStopsForTheCardThenReadsOnTryAnyway() {
        failProbe = true
        val c = controller()
        ready(c)
        val sent = CopyOnWriteArrayList<Boolean>()
        c.setLetterServer { _, stop ->
            sent += stop
            if (stop) LetterServerRead(400.0, "handwritten", null, true) else LetterServerRead(400.0, "handwritten", BILL, false)
        }
        c.attachServerLetter(photo())
        c.send("What does it say?")
        c.replied(1)
        assertEquals("handwriting", c.ui().conversation.messages.last().headsUp?.kind)
        c.pickHeadsUp("try_anyway")
        c.replied(2)
        assertEquals(listOf(true, false), sent)
        assertTrue(c.ui().conversation.messages.last().notes.any { it.code == "letter_handwriting" })
        // Gone with the server: the waiting letter goes too.
        c.attachServerLetter(photo())
        c.setLetterServer(null)
        assertNull(c.ui().pendingPhoto)
    }

    @Test fun neverSaysTheChipCantBeforeItsCheckHasRun() {
        val c = controller()
        until("installed") { c.ui().installed }
        assertEquals(VisionProbe.UNKNOWN, c.ui().vision)
        assertFalse(PhoneLetters.serverButtonShown(c.ui().copy(homeServerLetters = true)))
        c.send("What does this letter say?")
        c.replied(1)
        // The model loaded and the check ran first: it passed, so "take a photo".
        assertEquals(PhoneLetters.TAKE_A_PHOTO, c.ui().conversation.messages.last().text)
        assertEquals(PhoneLetters.CHECKING, PhoneLetters.noPhotoReply(true, VisionProbe.UNKNOWN, true))
    }

    companion object {
        /** A printed food label: a price, a weight, a date, no letter. */
        val LABEL = "Organic Rolled Oats\nNet Wt 18 oz (510 g)\nBest before 01 NOV 2026\n\$3.99\nKeep in a cool, dry place."
        val BILL = "RIVERSIDE ELECTRIC COOPERATIVE\nAccount number: 4471-2290-18\n" +
            "Service period: August 30, 2026 to September 29, 2026\nAmount due: \$142.17\n" +
            "Please pay by: November 15, 2026"
        val EXPLAINED = "FROM: Riverside Electric Cooperative\nABOUT: Your electricity bill.\n" +
            "DATE: D2 | due date\nAMOUNT: A1 | amount due\nAMOUNT: \$99.00 | invented\n" +
            "REFERENCE: Account number | 4471-2290-18\nTODO: Pay it. Pay \$99.00 today.\nUNCLEAR: nothing"
    }
}

/** A fake engine that reads letters: the one-word step, the transcription
 *  and the explanation, by the system prompt it is given. */
class LetterEngine(private val failProbe: Boolean) : LlmEngine {
    val photoCalls = CopyOnWriteArrayList<Triple<String, Int, String>>()
    val replies = CopyOnWriteArrayList<List<ChatTurn>>()
    val systems = CopyOnWriteArrayList<String>()
    @Volatile var kind = "printed"
    /** The other-way-round kind question's answer (null: as [kind]). */
    @Volatile var kindAgain: String? = null
    /** What the transcription reads. */
    @Volatile var transcript: String = PhoneLettersTest.BILL
    @Volatile override var isLoaded = false
    @Volatile private var vision: LlmBackend? = null

    override val seesPhotos: Boolean get() = isLoaded && vision != null

    override suspend fun load(model: File, backend: LlmBackend) = load(model, backend, null)

    override suspend fun load(model: File, backend: LlmBackend, vision: LlmBackend?) {
        this.vision = vision
        isLoaded = true
    }

    override suspend fun probeVision() {
        if (failProbe) throw IllegalStateException("no GPU here")
    }

    override fun replyToPhoto(system: String, jpeg: ByteArray, question: String, now: String?): Flow<String> =
        replyToPhoto(system, jpeg, question, now, 400)

    override fun replyToPhoto(system: String, jpeg: ByteArray, question: String, now: String?, maxOutput: Int): Flow<String> = flow {
        photoCalls += Triple(system, maxOutput, question)
        when (system) {
            LetterRead.KIND_SYSTEM -> emit(if (question == PhoneLetters.KIND_CHECK_QUESTION) kindAgain ?: kind else kind)
            LetterRead.TRANSCRIBE_SYSTEM -> transcript.chunked(13).forEach { emit(it) }
            else -> { emit("A cat"); emit(" on a sofa.") }
        }
    }

    override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = flow {
        replies += history
        systems += system
        if (system.startsWith("The person photographed a letter")) {
            PhoneLettersTest.EXPLAINED.chunked(17).forEach { emit(it) }
        } else {
            emit("Hello")
            emit(" there.")
        }
    }

    override fun cancel() {}

    override fun close() {
        isLoaded = false
        vision = null
    }
}
