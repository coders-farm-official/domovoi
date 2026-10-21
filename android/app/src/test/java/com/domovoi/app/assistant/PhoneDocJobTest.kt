package com.domovoi.app.assistant

import com.domovoi.app.assistant.docs.ChatDocStore
import com.domovoi.app.assistant.docs.DocLong
import com.domovoi.app.assistant.docs.DocPart
import com.domovoi.app.assistant.docs.DocSource
import com.domovoi.app.assistant.docs.PdfBlock
import com.domovoi.app.assistant.docs.PdfPageText
import com.domovoi.app.assistant.docs.PdfTextReader
import com.domovoi.app.assistant.docs.QaDoc
import com.domovoi.app.assistant.docs.SpeedRow
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
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
import java.nio.file.Files
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A long document summarized on the phone (documents plan D2): read in
 * parts then combined, each part's notes saved as it finishes, paused when
 * Chat leaves and carried on when it's back (or after the app was killed),
 * stopped keeping what was read, carried on, estimated from measured speeds,
 * and handed to the home server and back.
 */
class PhoneDocJobTest {
    private companion object {
        /** A different first word on every page (a line repeated on every
         *  page is a running header, and is left out). */
        val WORDS = listOf("", "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet",
            "Kilo", "Lima")
    }

    private val root: File = Files.createTempDirectory("doc-job").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val docs = ChatDocStore(File(root, "chat-docs/attachments"))
    private val settings = MemoryAssistantSettings()
    private val engine = JobEngine()
    private val scopes = CopyOnWriteArrayList<CoroutineScope>()

    private val llm = CatalogModel(
        "test-llm", "Test LLM", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "0".repeat(64))),
        "Apache-2.0", "https://example.org/license",
    )

    /** 12 pages of about 1,900 characters: 3 parts on the phone (10,000 a part). */
    private val pdf = PdfTextReader { _, _ ->
        (1..12).map { n ->
            val text = WORDS[n] + " rules. " +
                (1..30).joinToString(" ") { k -> "Clause $n.$k: the tenant of flat $n reports fault $k within ${k + n} days." }
            PdfPageText(listOf(PdfBlock(text, 50f, 100f, 550f, 112f)), 612f, 0)
        }
    }

    @After fun tearDown() {
        engine.releaseAll()
        scopes.forEach { it.cancel() }
        root.deleteRecursively()
    }

    private fun controller(): AssistantController {
        llm.files.forEach { f -> store.fileFor(llm, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(llm)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default).also { scopes += it }
        return AssistantController(
            memory = PhoneMemory((7.6 * (1L shl 30)).toLong(), lowRamDevice = false),
            store = store, conversations = conversations, jobs = FakeJobs(), settings = settings,
            newEngine = { engine }, scope = scope, models = listOf(llm), docs = docs, pdfText = { pdf },
        )
    }

    private fun until(what: String, timeoutMs: Long = 10_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ui() = state.value

    private fun AssistantController.ready() = until("installed") { ui().installed }

    private fun AssistantController.summarize() {
        attach("lease.pdf", { "%PDF-1.4\n".byteInputStream() }, question = "Summarize this")
    }

    private fun AssistantController.replies() = ui().conversation.messages.filter { !it.fromUser }

    @Test fun aLongSummaryIsReadInPartsThenCombined() {
        val c = controller()
        c.ready()
        c.screenShown()
        c.summarize()
        until("the summary") { c.replies().isNotEmpty() && !c.ui().replying }
        assertEquals(listOf("map", "map", "map", "reduce"), engine.kinds())
        // Each part saw only its own pages, as data in the marked block.
        val first = engine.users[0]
        assertTrue(first.contains("<<<PASSAGES") && first.contains("[p. 1]") && first.contains("[p. 5]") && !first.contains("[p. 6]"))
        assertTrue(engine.systems[0].endsWith(DocLong.MAP_RULES))
        // The reduce read the checked notes: the made-up page is gone.
        assertFalse(engine.users[3].contains("p. 99"))
        val reply = c.replies().last()
        assertEquals("summary", reply.docMode)
        assertFalse(reply.text.contains("p. 99"))
        assertEquals(listOf("p. 1", "p. 6"), reply.sources.map { it.label })
        assertTrue(reply.sources.all { it.cited } && reply.sources[1].page == 6)
        assertTrue(reply.notes.contains(DocLong.partsNote(3)))
        assertNull(c.ui().conversation.job)
        assertNull(c.ui().docJob)
        // The engine's figures are kept for the next estimate.
        assertNotNull(PhoneDocJobs.speed(settings.speedRows, LlmBackend.CPU))
    }

    @Test fun progressShowsThePartAndEachPartIsSavedAsItFinishes() {
        engine.holdFrom(1)
        val c = controller()
        c.ready()
        c.screenShown()
        c.summarize()
        until("part 2 held") { engine.calls() >= 2 }
        until("progress on part 2") { c.ui().docJob?.part == 2 }
        val v = c.ui().docJob!!
        assertEquals("reading", v.state)
        assertEquals(3, v.parts)
        assertTrue(v.line, v.line.startsWith("reading part 2 of 3"))
        // Part 1's notes are on disk already.
        val onDisk = conversations.load(c.ui().conversation.id)!!.job!!
        assertTrue(onDisk.notes[0]!!.contains("[p. 1]"))
        assertNull(onDisk.notes[1])
        engine.releaseAll()
        until("done") { c.replies().isNotEmpty() && !c.ui().replying }
    }

    @Test fun leavingChatPausesAtTheEndOfThePartAndComingBackCarriesOn() {
        engine.holdFrom(1)
        val c = controller()
        c.ready()
        c.screenShown()
        c.summarize()
        until("part 2 held") { engine.calls() >= 2 }
        c.screenHidden()
        engine.releaseAll()
        until("paused") { c.ui().conversation.job?.state == PhoneDocJob.STATE_PAUSED && !c.ui().replying }
        // Part 2 finished before the pause; part 3 wasn't started.
        assertEquals(2, engine.calls())
        val paused = conversations.load(c.ui().conversation.id)!!.job!!
        assertEquals(listOf(true, true, false), paused.notes.map { it != null })
        assertTrue(c.ui().docJob!!.line.startsWith("paused at part 3 of 3"))
        c.screenShown()
        until("the summary") { c.replies().isNotEmpty() && !c.ui().replying }
        assertEquals(listOf("map", "map", "map", "reduce"), engine.kinds())
    }

    @Test fun aKilledAppCarriesOnFromItsNextPart() {
        engine.holdFrom(1)
        val a = controller()
        a.ready()
        a.screenShown()
        a.summarize()
        until("part 2 held") { engine.calls() >= 2 }
        val id = a.ui().conversation.id
        // The process dies mid part 2: only part 1 is on disk.
        scopes.first().cancel()
        Thread.sleep(100)
        engine.releaseAll()
        engine.calls.clear()
        val b = controller()
        until("restored") { b.ui().conversation.id == id }
        b.ready()
        b.screenShown()
        until("the summary") { b.replies().isNotEmpty() && !b.ui().replying }
        assertEquals(listOf("map", "map", "reduce"), engine.kinds())
        assertTrue(engine.users.last().contains("[p. 1]"))
    }

    @Test fun stopKeepsWhatWasReadAndCarryOnReadsOnlyTheRest() {
        engine.holdFrom(1)
        val c = controller()
        c.ready()
        c.screenShown()
        c.summarize()
        until("part 2 held") { engine.calls() >= 2 }
        c.stop()
        until("stopped") { c.replies().isNotEmpty() && !c.ui().replying }
        val stopped = c.replies().last()
        assertTrue(stopped.text, stopped.text.startsWith("**Summary of pages 1-5 (stopped)**"))
        assertTrue(stopped.text.contains("[p. 1]") && !stopped.text.contains("p. 99"))
        assertTrue(stopped.notes.any { it.code == "stopped" && it.text.contains("part 1 of 3") })
        val job = c.ui().conversation.job!!
        assertEquals(PhoneDocJob.STATE_STOPPED, job.state)
        assertEquals(job.id, stopped.jobId)
        assertEquals(1, job.read)
        engine.releaseAll()
        engine.calls.clear()
        c.carryOnJob()
        until("the whole summary") { c.replies().size == 2 && !c.ui().replying }
        assertEquals(listOf("map", "map", "reduce"), engine.kinds())
        assertNull(c.ui().conversation.job)
        assertFalse(c.replies().last().text.contains("stopped"))
    }

    @Test fun theEstimateComesFromMeasuredSpeeds() {
        // Nothing measured: no time shown until a part has been read.
        val c = controller()
        c.ready()
        c.screenShown()
        engine.holdFrom(0)
        c.summarize()
        until("part 1 held") { engine.calls() >= 1 && c.ui().docJob != null }
        assertNull(c.ui().docJob!!.etaS)
        assertEquals("reading part 1 of 3", c.ui().docJob!!.line)
        assertNull(c.ui().conversation.job!!.estimateMs)
        engine.releaseAll()
        until("done") { c.replies().isNotEmpty() && !c.ui().replying }
        // Measured now: the next job starts with an estimate.
        engine.calls.clear()
        engine.holdFrom(0)
        c.newConversation()
        until("new") { c.ui().conversation.messages.isEmpty() }
        c.summarize()
        until("part 1 held again") { engine.calls() >= 1 && c.ui().docJob != null }
        assertNotNull(c.ui().conversation.job!!.estimateMs)
        assertTrue(c.ui().docJob!!.line, c.ui().docJob!!.line.contains("left"))
        engine.releaseAll()
    }

    @Test fun aJobHandedToTheHomeServerComesBackAsItsSummary() {
        engine.holdFrom(1)
        val c = controller()
        c.ready()
        c.screenShown()
        c.summarize()
        until("part 2 held") { engine.calls() >= 2 }
        assertNotNull(c.handOverBytes())
        val h = runBlocking { c.handOverToServer() }!!
        assertEquals("lease.pdf", h.name)
        assertEquals("Summarize this", h.question)
        assertTrue(h.file.isFile)
        until("handed over") { c.ui().conversation.job?.state == PhoneDocJob.STATE_SERVER && !c.ui().replying }
        assertEquals("server", c.ui().docJob!!.state)
        c.serverJobStarted(h.conversation, "a".repeat(32))
        until("server id kept") { c.ui().conversation.job?.serverJob == "a".repeat(32) }
        c.serverProgress(h.conversation, "reading part 2 of 3 · about 10 s left", 1, 3, 10.0)
        assertEquals("reading part 2 of 3 · about 10 s left", c.ui().docJob!!.line)
        c.serverFinished(h.conversation, "- Rent [p. 1]", listOf(DocSource(label = "p. 1", text = "Clause 1", cited = true, page = 1)),
            emptyList(), stopped = false)
        until("the server's summary") { c.replies().isNotEmpty() }
        val reply = c.replies().last()
        assertEquals("- Rent [p. 1]", reply.text)
        assertTrue(reply.notes.contains(PhoneDocJobs.SERVER_NOTE))
        assertNull(c.ui().conversation.job)
        assertNull(c.ui().docJob)
    }

    @Test fun aHomeServerThatFailsHandsTheJobBackToThePhone() {
        engine.holdFrom(1)
        val c = controller()
        c.ready()
        c.screenShown()
        c.summarize()
        until("part 2 held") { engine.calls() >= 2 }
        val h = runBlocking { c.handOverToServer() }!!
        until("handed over") { c.ui().conversation.job?.state == PhoneDocJob.STATE_SERVER && !c.ui().replying }
        engine.releaseAll()
        engine.calls.clear()
        c.serverFailed(h.conversation, "the home server isn't in reach")
        until("read on the phone") { c.replies().isNotEmpty() && !c.ui().replying }
        // Part 1 was kept; parts 2 and 3 were read here.
        assertEquals(listOf("map", "map", "reduce"), engine.kinds())
    }

    @Test fun theOfferAppearsOnlyWhenTheServerIsMeasuredFaster() {
        assertEquals(25.0 + 1.0, PhoneDocJobs.offer(130.0, 25.0, 1_500_000, serverAi = true)!!, 1e-9)
        assertNull(PhoneDocJobs.offer(30.0, 25.0, 15_000_000, serverAi = true))     // sending eats the gain
        assertNull(PhoneDocJobs.offer(130.0, 420.0, 1_000, serverAi = true))        // a slow CPU box
        assertNull(PhoneDocJobs.offer(130.0, 25.0, 1_000, serverAi = false))        // AI off at home
        assertNull(PhoneDocJobs.offer(null, 25.0, 1_000, serverAi = true))          // nothing measured here yet
        assertNull(PhoneDocJobs.offer(130.0, null, 1_000, serverAi = true))         // nothing measured there
        assertEquals("Sends lease.pdf to your home server to read. Nothing is kept there.", PhoneDocJobs.handOverText("lease.pdf"))
    }

    @Test fun speedRowsComeFromTheEngineAndOnlyTheNewestAreKept() {
        val row = PhoneDocJobs.row(LlmStats(1.0, 3000, 150, 300.0, 30.0), 12000)!!
        assertEquals(10_000.0, row.promptMs!!, 1e-6)
        assertEquals(5_000.0, row.generateMs!!, 1e-6)
        assertNull(PhoneDocJobs.row(LlmStats(0.0, 0, 0, 0.0, 0.0), 10))
        var text = ""
        repeat(25) { text = PhoneDocJobs.addRow(text, LlmBackend.GPU, row) }
        assertEquals(PhoneDocJobs.MAX_SPEED_ROWS, PhoneDocJobs.readRows(text)["GPU"]!!.size)
        assertNull(PhoneDocJobs.speed(text, LlmBackend.CPU))
        val s = PhoneDocJobs.speed(text, LlmBackend.GPU)!!
        assertEquals(300.0, s.prefillTps, 1e-6)
        assertEquals(30.0, s.decodeTps, 1e-6)
        assertEquals(4.0, s.charsPerToken, 1e-6)
        assertEquals(SpeedRow(2.0, 4.0, null, null, 1.0), PhoneDocJobs.plus(SpeedRow(1.0, 2.0, promptChars = 1.0), SpeedRow(1.0, 2.0)))
    }

    @Test fun theViewWordsTheStates() {
        val d = listOf(QaDoc("x.pdf", (1..4).map { DocPart("p. $it", "Page $it. " + "word ".repeat(600), page = it) }))
        val chunks = DocLong.planChunks(d, 400)
        val job = PhoneDocJob(id = "j", chunks = chunks, notes = List(chunks.size) { null }, docIds = listOf("d"))
        val v = PhoneDocJobs.view("c", job, listOf("x.pdf"), "reading", 61.0)
        assertEquals("reading part 1 of ${chunks.size} · about 1 min left", v.line)
        assertTrue(v.single)
        val p = PhoneDocJobs.view("c", job.copy(notes = listOf("a") + List(chunks.size - 1) { null }), listOf("x.pdf"), "paused", null)
        assertEquals("paused at part 2 of ${chunks.size} · carries on when you come back", p.line)
    }
}

/** An engine that writes notes naming the first page it was given (and a
 *  made-up page the app must drop), and a summary citing pages 1 and 6.
 *  Calls from [holdFrom] on wait until released. */
class JobEngine : LlmEngine {
    val calls = CopyOnWriteArrayList<String>()
    val users = CopyOnWriteArrayList<String>()
    val systems = CopyOnWriteArrayList<String>()
    private val gates = ConcurrentHashMap<Int, CompletableDeferred<Unit>>()
    @Volatile private var holdFrom = Int.MAX_VALUE
    @Volatile override var isLoaded = false

    fun holdFrom(index: Int) {
        holdFrom = calls.size + index
    }

    fun releaseAll() {
        holdFrom = Int.MAX_VALUE
        gates.values.forEach { it.complete(Unit) }
        gates.clear()
    }

    fun calls(): Int = calls.size

    fun kinds(): List<String> = calls.toList()

    override suspend fun load(model: File, backend: LlmBackend) {
        isLoaded = true
    }

    override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = flow {
        val user = history.last().text
        val kind = when {
            system.endsWith(DocLong.MAP_RULES) -> "map"
            system.endsWith(DocLong.REDUCE_RULES) -> "reduce"
            else -> "summary"
        }
        val index = calls.size
        calls += kind
        users += user
        systems += system
        if (index >= holdFrom) gates.getOrPut(index) { CompletableDeferred() }.await()
        val text = when (kind) {
            "map" -> {
                val label = Regex("""\[(p\. \d+)]""").find(user)?.groupValues?.get(1) ?: "p. 1"
                "- The part from $label covers repairs [$label]\n- A pool on the roof [p. 99]"
            }
            else -> "- Faults are reported quickly [p. 1]\n- Flats keep their items [p. 6]\n- Free parking [p. 99]"
        }
        text.chunked(7).forEach { emit(it) }
    }

    override fun cancel() {}

    override fun lastStats(): LlmStats = LlmStats(0.5, 2500, 60, 250.0, 20.0)

    override fun close() {
        isLoaded = false
    }
}
