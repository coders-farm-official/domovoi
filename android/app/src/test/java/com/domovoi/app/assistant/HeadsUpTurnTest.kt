package com.domovoi.app.assistant

import com.domovoi.app.assistant.docs.ChatDocStore
import com.domovoi.app.assistant.docs.DocCalc
import com.domovoi.app.assistant.docs.DocFixtures
import com.domovoi.app.assistant.docs.HeadsUpStore
import com.domovoi.app.assistant.docs.WeakSpots
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The honest heads-up in a phone chat (documents plan D3): the card comes
 * from the measured table, with no model asked; "calculate exactly" sends
 * the listing rules and the app adds up the checked numbers; a kind shows
 * once per chat; this phone's "don't warn me" and master switch keep only
 * the note; "try anyway" on a whole-document rewrite says what was left out.
 */
class HeadsUpTurnTest {
    private val root: File = Files.createTempDirectory("heads-up-turn").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val docs = ChatDocStore(File(root, "chat-docs/attachments"))
    private val engines = CopyOnWriteArrayList<ScriptedEngine>()
    @Volatile private var script = listOf("- \$1,200 [Section: Rent] monthly rent\n- \$1,500 [Section: Deposit] deposit")
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val headsUp = HeadsUpStore.memory()
    private val table = WeakSpots.parse(
        listOf("../../domovoi/doc_qa/chat-weak-spots.json", "../domovoi/doc_qa/chat-weak-spots.json")
            .map(::File).first { it.isFile }.readText(),
    )

    private val llm = CatalogModel(
        "test-llm", "Test LLM", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "0".repeat(64))),
        "Apache-2.0", "https://example.org/license",
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    private fun controller(): AssistantController {
        llm.files.forEach { f -> store.fileFor(llm, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(llm)
        return AssistantController(
            memory = PhoneMemory((7.6 * (1L shl 30)).toLong(), lowRamDevice = false),
            store = store, conversations = conversations, jobs = FakeJobs(), settings = MemoryAssistantSettings(),
            newEngine = { ScriptedEngine().also { it.script = script; engines += it } },
            scope = scope, models = listOf(llm), docs = docs, headsUp = headsUp, weakSpots = { table },
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

    private fun lease(filler: Int = 0): ByteArray = DocFixtures.docx(
        DocFixtures.p("Rent", "Heading1") + DocFixtures.p("The monthly rent is \$1,200, due on the first day.") +
            DocFixtures.p("Deposit", "Heading1") + DocFixtures.p("The security deposit is \$1,500, returned within 30 days.") +
            (1..filler).joinToString("") { n ->
                DocFixtures.p("Part $n", "Heading1") +
                    DocFixtures.p("Residents of block $n share the garden, the laundry room and the bicycle store, " +
                        "and keep the hallways of block $n clear of boxes, shoes and parcels at all times of day.")
            },
    )

    private val calcQ = "What is the total of the rent and the deposit?"

    @Test fun aSumGetsItsCardAndNoModelIsAsked() {
        val c = controller()
        until("installed") { c.ui().installed }
        c.attach("lease.docx", { lease().inputStream() }, question = calcQ)
        c.replied(1)
        val card = c.ui().conversation.messages.last()
        assertEquals("heads_up", card.docMode)
        val hu = assertNotNull(card.headsUp).let { card.headsUp!! }
        assertEquals("calc", hu.kind)
        assertEquals(listOf(listOf("calculate", "calculate exactly"), listOf("try_anyway", "try anyway")), hu.options)
        assertTrue(engines.isEmpty())
        // A card is answered by its options, not tried again.
        assertTrue(!c.canRetry())
    }

    @Test fun calculateExactlyListsTheNumbersAndTheAppAddsThemUp() {
        val c = controller()
        until("installed") { c.ui().installed }
        c.attach("lease.docx", { lease().inputStream() }, question = calcQ)
        c.replied(1)
        c.pickHeadsUp("calculate")
        c.replied(2)
        val msgs = c.ui().conversation.messages
        assertEquals("calculate", msgs[msgs.size - 2].headsUp?.chosen)
        val reply = msgs.last()
        assertTrue(reply.text, reply.text.startsWith("**Total: \$2,700 (1,200 + 1,500 = 2,700)**"))
        assertTrue(reply.notes.any { it.code == "calc_worked" })
        assertTrue(engines.single().systems.single().contains("Don't calculate anything"))
        // A kind shows once per chat: the next sum goes ahead, with the note.
        script = listOf("Together \$2,700 [Section: Rent].")
        c.send("What is the total of the deposit and the rent?")
        c.replied(3)
        val next = c.ui().conversation.messages.last()
        assertNull(next.headsUp)
        assertTrue(next.notes.any { it.code == "heads_up_calc" })
    }

    @Test fun dontWarnMeAndTheSwitchLeaveTheGuardingNote() {
        headsUp.neverWarn("calc")
        script = listOf("Together \$2,700 [Section: Rent].")
        val c = controller()
        until("installed") { c.ui().installed }
        c.attach("lease.docx", { lease().inputStream() }, question = calcQ)
        c.replied(1)
        val reply = c.ui().conversation.messages.last()
        assertNull(reply.headsUp)
        assertTrue(reply.notes.any { it.code == "heads_up_calc" })
        headsUp.warnAgain("calc")
        headsUp.setOff(true)
        assertTrue(c.headsUpPrefs.value.off)
    }

    @Test fun tryAnywayOnAWholeDocumentRewriteSaysWhatWasLeftOut() {
        script = listOf("The rent is \$1,200 a month [Section: Rent].")
        val c = controller()
        until("installed") { c.ui().installed }
        c.attach("lease.docx", { lease(filler = 40).inputStream() }, question = "Rewrite this in plain English")
        c.replied(1)
        val card = c.ui().conversation.messages.last().headsUp
        assertEquals("rewrite", card?.kind)
        assertEquals(listOf(listOf("try_anyway", "try anyway")), card?.options)
        assertTrue(engines.isEmpty())
        c.pickHeadsUp("try_anyway")
        c.replied(2)
        assertTrue(c.ui().conversation.messages.last().notes.any { it.code == "heads_up_rewrite" })
        // Its words matched nothing: the model got the document's start.
        assertTrue(engines.single().replies.single()[0].text.contains("[Section: Rent]"))
    }

    @Test fun aPickThatIsNoOptionOrOnAnAnsweredCardDoesNothing() {
        val c = controller()
        until("installed") { c.ui().installed }
        c.attach("lease.docx", { lease().inputStream() }, question = calcQ)
        c.replied(1)
        c.pickHeadsUp("home_server")
        Thread.sleep(50)
        assertNull(c.ui().conversation.messages.last().headsUp?.chosen)
        assertTrue(engines.isEmpty())
    }

    @Test fun theListingRulesAreTheServers() {
        assertTrue(DocCalc.buildPrompt("q", emptyList(), emptyList(), "sum", nonce = "0").rules.startsWith(
            "The person attached a document and asks for a calculation. Don't calculate anything"))
    }
}
