package com.domovoi.app.assistant

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
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
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList

/**
 * A phone chat with N1's phone facts, through the real controller: the fast
 * paths answer with no model, the model is told the facts and offered the
 * read tools, a permission is asked once and a refusal is kept, and hand-off
 * cards are made only when an app takes them.
 */
class PhoneChatControllerTest {
    private val root: File = Files.createTempDirectory("phone-chat").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val settings = MemoryAssistantSettings()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val engines = CopyOnWriteArrayList<TurnEngine>()
    private val la = ZoneId.of("America/Los_Angeles")
    private val nowMs = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la).toInstant().toEpochMilli()
    private val phone = FakePhone()
    @Volatile private var script: suspend (ModelTurn, (String) -> Unit) -> Unit = { _, emit -> emit("Hello there.") }

    private val llm = CatalogModel(
        "test-llm", "Test LLM (instruction-tuned)", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "0".repeat(64))),
        "Apache-2.0", "https://example.org/license",
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    /** Records each turn; [script] writes the reply (and may call tools). */
    inner class TurnEngine : LlmEngine {
        val turns = CopyOnWriteArrayList<ModelTurn>()
        override var isLoaded = false
        override suspend fun load(model: File, backend: LlmBackend) {
            isLoaded = true
        }
        override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = error("the N1 path sends a ModelTurn")
        override fun reply(turn: ModelTurn): Flow<String> = flow {
            turns += turn
            val out = mutableListOf<String>()
            script(turn) { out += it }
            out.forEach { emit(it) }
        }
        override fun cancel() {}
        override fun close() {
            isLoaded = false
        }
    }

    private fun controller(): AssistantController {
        llm.files.forEach { f -> store.fileFor(llm, f).apply { parentFile!!.mkdirs(); writeBytes(ByteArray(f.size.toInt())) } }
        store.markInstalled(llm)
        return AssistantController(
            memory = PhoneMemory((7.6 * (1L shl 30)).toLong(), lowRamDevice = false),
            store = store,
            conversations = conversations,
            jobs = FakeJobs(),
            settings = settings,
            newEngine = { TurnEngine().also { engines += it } },
            scope = scope,
            models = listOf(llm),
            clock = { nowMs },
            clockStyle = { ClockStyle(la, Locale.US, is24Hour = false) },
            phone = phone,
        ).also { c -> until("installed") { c.state.value.installed } }
    }

    private fun until(what: String, timeoutMs: Long = 5_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeoutMs
        while (!ok()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for: $what")
            Thread.sleep(5)
        }
    }

    private fun AssistantController.ask(text: String): StoredMessage {
        val before = state.value.conversation.messages.size
        send(text)
        until("the reply to $text") { !state.value.replying && state.value.conversation.messages.size >= before + 2 }
        return state.value.conversation.messages.last()
    }

    // ── Fast paths ──────────────────────────────────────────────────────

    @Test fun phoneQuestionsAreAnsweredWithNoModel() {
        val c = controller()
        val a = c.ask("How much battery do I have?")
        assertEquals("The battery is at 82% and charging.", a.text)
        assertEquals("battery", a.fastPath)
        assertNull(a.backend)
        assertNotNull(a.firstTextMs)
        assertEquals("which model", "I'm Domovoi, running Test LLM on this phone's CPU, with no internet.", c.ask("which model are you").text)
        assertTrue("no model was loaded for them", engines.isEmpty())
        assertEquals(ReplyReport.PHONE, c.draftReport(c.state.value.conversation, 1)!!.model)
    }

    // ── The model: facts and read tools ─────────────────────────────────

    @Test fun theModelIsToldTheFactsAndOfferedTheReadTools() {
        phone.facts = PromptFacts(NowPlayingFact("Glass Harbor", "Velvet Kites"), server = ServerLink.CONNECTED,
            rooms = listOf("kitchen"), listener = "Kamron")
        val c = controller()
        c.ask("Tell me a joke")
        val turn = engines.single().turns.single()
        assertEquals(Persona.SYSTEM_PROMPT, turn.system)
        assertEquals(listOf(ContextLine.CLOCK, ContextLine.FACTS), turn.context.map { it.kind })
        assertEquals(PhoneFacts.line(phone.facts), turn.context[1].text)
        assertEquals(listOf(PhoneTools.FIND_MUSIC, PhoneTools.FIND_VIDEO, PhoneTools.LIST_TIMERS, PhoneTools.PHONE_STATUS), turn.tools!!.names)
    }

    @Test fun aToolCallRunsTheReadAndIsNamedOnTheReply() {
        phone.library.music = listOf(FoundItem("Glass Harbor", "Velvet Kites"))
        script = { turn, emit ->
            turn.onTool(PhoneTools.FIND_MUSIC)
            val r = turn.tools!!.run(PhoneTools.FIND_MUSIC, mapOf("query" to "velvet"))
            emit("You have ${r["count"]} song by Velvet Kites.")
        }
        val c = controller()
        val a = c.ask("Do I have any songs by Velvet Kites?")
        assertEquals("You have 1 song by Velvet Kites.", a.text)
        assertEquals(listOf(PhoneTools.FIND_MUSIC), a.tools)
        assertEquals(listOf("music:velvet"), phone.library.queries)
        assertNull(a.fastPath)
    }

    @Test fun onTheGpuNoToolsAreOffered() {
        settings.backend = LlmBackend.GPU
        val c = controller()
        c.ask("Tell me a joke")
        assertNull(engines.single().turns.single().tools)
    }

    @Test fun anEmptyReplyAfterAToolSaysSo() {
        script = { turn, _ -> turn.onTool(PhoneTools.PHONE_STATUS) }
        val c = controller()
        assertEquals(AssistantController.NO_TOOL_ANSWER, c.ask("hmm what about the phone stuff").text)
    }

    // ── Permissions ─────────────────────────────────────────────────────

    @Test fun aPermissionIsAskedOnceThenTheQuestionIsAnswered() {
        val c = controller()
        val ask = c.ask("What's on my calendar?")
        assertEquals(PhonePermissions.reason(PhonePermission.CALENDAR), ask.text)
        val card = ask.card!!
        assertEquals(CardKind.PERMISSION, card.kind)
        assertEquals(PhonePermission.CALENDAR, card.phonePermission)
        assertEquals(CardState.OPEN, card.state)
        assertFalse(phone.reader.reads.contains("calendar"))
        // Android's dialog said yes.
        phone.permissions[PhonePermission.CALENDAR] = PermissionState.GRANTED
        c.permissionDecided(c.state.value.conversation.id, 1, granted = true)
        until("answered") { c.state.value.conversation.messages.size == 3 && !c.state.value.replying }
        val msgs = c.state.value.conversation.messages
        assertEquals(CardState.DONE, msgs[1].card!!.state)
        assertEquals("Nothing else is on this phone's calendar today.", msgs[2].text)
        assertTrue(engines.isEmpty())
    }

    @Test fun aRefusalIsKeptAndNeverAskedAgain() {
        phone.serverSet = true
        val c = controller()
        c.ask("Where am I?")
        c.permissionDecided(c.state.value.conversation.id, 1, granted = false)
        until("answered") { c.state.value.conversation.messages.size == 3 && !c.state.value.replying }
        assertEquals(listOf(PhonePermission.LOCATION), phone.declinedList)
        val msgs = c.state.value.conversation.messages
        assertEquals(CardState.DISMISSED, msgs[1].card!!.state)
        assertTrue(msgs[2].text.startsWith("I can't see where the phone is"))
        val again = c.ask("where am I")
        assertNull("no second card", again.card)
        assertTrue(again.text.startsWith("I can't see where the phone is"))
    }

    // ── Hand-offs ───────────────────────────────────────────────────────

    @Test fun aHandOffIsACardFirst() {
        val c = controller()
        val a = c.ask("Directions to Pike Place Market")
        assertEquals("handoff", a.fastPath)
        assertEquals(HandOff(HandOffKind.DIRECTIONS, place = "Pike Place Market"), a.card!!.handOff)
        assertEquals(CardState.OPEN, a.card!!.state)
        c.handOffTapped(c.state.value.conversation.id, 1, done = true)
        until("marked") { c.state.value.conversation.messages[1].card!!.state == CardState.DONE }
        assertTrue(engines.isEmpty())
    }

    @Test fun aHandOffNoAppTakesIsNotOffered() {
        phone.handOffs = emptySet()
        val c = controller()
        val a = c.ask("navigate to the airport")
        assertEquals(HandOffs.noApp(HandOffKind.DIRECTIONS), a.text)
        assertNull(a.card)
    }

    @Test fun callingByNameAsksForContactsOnceThenUsesThePicker() {
        val c = controller()
        val ask = c.ask("Call Sam")
        assertEquals(PhonePermission.CONTACTS, ask.card!!.phonePermission)
        c.permissionDecided(c.state.value.conversation.id, 1, granted = false)
        until("answered") { c.state.value.conversation.messages.size == 3 && !c.state.value.replying }
        val card = c.state.value.conversation.messages[2].card!!
        assertEquals(HandOff(HandOffKind.CALL, who = "Sam"), card.handOff)
        assertTrue("the picker chooses the person", card.handOff!!.needsContact)
        assertFalse(phone.reader.reads.contains("contacts"))
        // Next time: straight to the card, no asking.
        assertEquals(CardKind.HANDOFF, c.ask("text Sam").card!!.kind)
    }

    @Test fun withContactsTheNumberIsFilledIn() {
        phone.permissions[PhonePermission.CONTACTS] = PermissionState.GRANTED
        phone.reader.people = listOf(ContactMatch("Samantha Jones", "555 0199", null), ContactMatch("Sam Lee", "555 0123", null, "mobile"))
        val c = controller()
        val h = c.ask("Text Sam I'm on my way").card!!.handOff!!
        assertEquals("Sam Lee", h.name)
        assertEquals("555 0123", h.number)
        assertEquals("I'm on my way", h.body)
    }

    // ── Never blended ───────────────────────────────────────────────────

    /** The phone chat's facts come from the phone only: nothing in its
     *  context names a server address, a household calendar or another
     *  place's chats, whatever the server is called. */
    @Test fun aPhoneChatsContextIsThePhonesOwn() {
        phone.serverSet = true
        phone.facts = PromptFacts(server = ServerLink.NOT_CONNECTED)
        val c = controller()
        c.ask("What should I cook tonight?")
        val turn = engines.single().turns.single()
        val told = turn.system + turn.context.joinToString { it.text }
        listOf("calendar", "http", "6369", "chat history").forEach { assertFalse(it, told.contains(it, ignoreCase = true)) }
        assertTrue(told, told.contains("not connected to the home server", ignoreCase = true))
    }
}
