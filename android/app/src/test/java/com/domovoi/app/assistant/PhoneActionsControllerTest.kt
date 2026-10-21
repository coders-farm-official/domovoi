package com.domovoi.app.assistant

import com.domovoi.app.player.Chapter
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayKind
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

/** The player as the test sets it; every call is recorded. */
class FakePlayer : PhonePlayer {
    @Volatile var state = PlayerSnapshot(emptyList(), 0, 0.0, playing = false, sleepSec = null)
    @Volatile var casting: String? = null
    val calls = CopyOnWriteArrayList<String>()

    override suspend fun snapshot() = state
    override suspend fun castingTo() = casting
    override suspend fun pause() { calls += "pause"; state = state.copy(playing = false) }
    override suspend fun resume() { calls += "resume"; state = state.copy(playing = true) }
    override suspend fun next() { calls += "next"; state = state.copy(index = (state.index + 1).coerceAtMost(state.queue.lastIndex), positionSec = 0.0) }
    override suspend fun previous() { calls += "previous"; state = state.copy(index = (state.index - 1).coerceAtLeast(0), positionSec = 0.0) }
    override suspend fun stop() { calls += "stop"; state = PlayerSnapshot(emptyList(), 0, 0.0, false, state.sleepSec) }
    override suspend fun sleep(minutes: Int) { calls += "sleep:$minutes"; state = state.copy(sleepSec = minutes * 60) }
    override suspend fun cancelSleep() { calls += "cancelSleep"; state = state.copy(sleepSec = null) }
    override suspend fun play(items: List<PlayItem>, start: Int, positionSec: Double, speed: Float?) {
        calls += "play:${items.size}@$start+${positionSec.toInt()}"
        state = PlayerSnapshot(items, start, positionSec, playing = true, sleepSec = state.sleepSec, speed = speed ?: 1f)
    }
    override suspend fun restore(s: PlayerSnapshot) { calls += "restore"; state = s }
}

/** The Clock app, the player and the saved things, as the test sets them. */
class FakeActionHost(
    var clockIntents: Set<ClockIntent> = ClockIntent.entries.toSet(),
    var clockWorks: Boolean = true,
    override val player: FakePlayer? = FakePlayer(),
    var songs: List<PlayItem>? = emptyList(),
    var books: List<BookChoice> = emptyList(),
    var videos: List<VideoChoice>? = emptyList(),
) : PhoneActionHost {
    val sent = CopyOnWriteArrayList<ClockRequest>()
    override fun clockTakes(intent: ClockIntent) = intent in clockIntents
    override suspend fun clock(request: ClockRequest): Boolean {
        sent += request
        return clockWorks
    }
    override suspend fun songs(query: String, limit: Int): List<PlayItem>? = songs?.filter {
        query.isBlank() || it.title.contains(query, true) || it.artist?.contains(query, true) == true || it.album?.contains(query, true) == true
    }?.take(limit)
    override suspend fun books() = books
    override suspend fun videos(query: String, limit: Int) = videos?.filter { it.title.contains(query, true) }?.take(limit)
}

/**
 * N2's actions through the real controller: asked first (a card, a typed
 * or spoken yes or no), done at once with Undo, hidden when no app takes
 * them, and never a model tool call on E2B.
 */
class PhoneActionsControllerTest {
    private val root: File = Files.createTempDirectory("phone-actions").toFile()
    private val store = ModelStore(File(root, "models"))
    private val conversations = ConversationStore(File(root, "assistant"))
    private val settings = MemoryAssistantSettings()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val engines = CopyOnWriteArrayList<TurnEngine>()
    private val la = ZoneId.of("America/Los_Angeles")
    @Volatile private var nowMs = ZonedDateTime.of(2026, 10, 21, 15, 42, 0, 0, la).toInstant().toEpochMilli()
    private val phone = FakePhone()
    private val host = FakeActionHost()
    private val player get() = host.player!!
    @Volatile private var script: suspend (ModelTurn, (String) -> Unit) -> Unit = { _, emit -> emit("Hello there.") }

    @Volatile private var llm = CatalogModel(
        "test-llm", "Test LLM (instruction-tuned)", ModelKind.LLM,
        listOf(ModelFile("model.litertlm", "https://example.org/model.litertlm", 100, "0".repeat(64))),
        "Apache-2.0", "https://example.org/license",
    )

    @After fun tearDown() {
        scope.cancel()
        root.deleteRecursively()
    }

    inner class TurnEngine : LlmEngine {
        val turns = CopyOnWriteArrayList<ModelTurn>()
        override var isLoaded = false
        override suspend fun load(model: File, backend: LlmBackend) {
            isLoaded = true
        }
        override fun reply(system: String, history: List<ChatTurn>, now: String?): Flow<String> = error("sends a ModelTurn")
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
            actions = host,
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
        until("the reply to $text", 20_000) { !state.value.replying && state.value.conversation.messages.size >= before + 2 }
        return state.value.conversation.messages.last()
    }

    private fun AssistantController.card(index: Int): PhoneCard? = state.value.conversation.messages[index].card

    private fun song(id: Long, title: String, artist: String? = null) =
        PlayItem.fromDeviceAudio(id, title, artist, null, 180.0, "content://media/external/audio/media/$id", null)

    private fun playing(vararg titles: String, index: Int = 0, playing: Boolean = true, pos: Double = 30.0) {
        player.state = PlayerSnapshot(titles.mapIndexed { i, t -> song(i + 1L, t) }, index, pos, playing, sleepSec = null)
    }

    // ── Asked first ─────────────────────────────────────────────────────

    @Test fun aTimerAsksFirstAndAClearYesSetsIt() {
        val c = controller()
        val q = c.ask("Set a timer for 10 minutes for the pasta")
        assertEquals("Set a timer for 10 minutes called \"pasta\" in the Clock app?", q.text)
        assertEquals(CardKind.ACTION, q.card?.kind)
        assertEquals(CardState.OPEN, q.card?.state)
        assertEquals("Timer: 10 minutes, pasta", q.card?.title)
        assertTrue("nothing is set before the yes", host.sent.isEmpty())
        val done = c.ask("yes")
        assertEquals("Done: a 10 minutes timer called \"pasta\" is set in the Clock app.", done.text)
        assertEquals(listOf(ClockRequest(ClockIntent.SET_TIMER, seconds = 600, label = "pasta")), host.sent)
        assertEquals(CardState.DONE, c.card(1)?.state)
        assertTrue("no model was loaded", engines.isEmpty())
    }

    @Test fun anyNoWordCancelsAndAnUnclearReplyAsksAgain() {
        val c = controller()
        c.ask("wake me at 6:30")
        val again = c.ask("hmm")
        assertTrue(again.text, again.text.startsWith("I didn't catch that. Set an alarm for 6:30 AM tomorrow in the Clock app?"))
        assertEquals(CardState.REPLACED, c.card(1)?.state)
        assertEquals(CardState.OPEN, again.card?.state)
        // "yes, no wait" has a no-word: it's a no.
        val no = c.ask("yes, no wait")
        assertEquals(ActionTexts.DECLINED, no.text)
        assertEquals(CardState.DISMISSED, c.card(3)?.state)
        assertTrue(host.sent.isEmpty())
        // "yes" with something after it isn't a clear yes either.
        c.ask("set an alarm for 7 am")
        assertTrue(c.ask("yes and play some jazz").text.startsWith(ActionTexts.DIDNT_CATCH))
        assertTrue(host.sent.isEmpty())
        // A longer sentence is a new question: the card stays as it was.
        script = { _, emit -> emit("Rome is the capital of Italy.") }
        assertEquals("Rome is the capital of Italy.", c.ask("what is the capital of italy and why is it famous").text)
        assertEquals(CardState.REPLACED, c.card(7)?.state)
        assertEquals(CardState.OPEN, c.card(9)?.state)
    }

    @Test fun aSpokenTurnHearsTheQuestionAndAYesOrNo() {
        val c = controller()
        val heard = CopyOnWriteArrayList<String>()
        c.speakForTest("set an alarm for 7 am") { heard += it }
        assertEquals("Set an alarm for 7:00 AM tomorrow in the Clock app? Say yes or no.", heard.joinToString(""))
        heard.clear()
        c.speakForTest("Yes.") { heard += it }
        assertEquals("Done: an alarm for 7:00 AM is set in the Clock app.", heard.joinToString(""))
        assertEquals(ClockRequest(ClockIntent.SET_ALARM, hour = 7, minute = 0), host.sent.single())
    }

    @Test fun aVagueTimeIsAskedAbout() {
        val c = controller()
        val q = c.ask("Wake me a bit before seven tomorrow")
        assertEquals("How about 6:45 AM? Set an alarm for 6:45 AM tomorrow in the Clock app?", q.text)
        c.ask("sure")
        assertEquals(ClockRequest(ClockIntent.SET_ALARM, hour = 6, minute = 45), host.sent.single())
    }

    @Test fun repeatingAlarmsCarryTheirDays() {
        val c = controller()
        assertEquals("Set an alarm for 6:30 AM every weekday in the Clock app?", c.ask("wake me at 6:30 every weekday").text)
        c.ask("ok")
        assertEquals(listOf(2, 3, 4, 5, 6), host.sent.single().days)
    }

    @Test fun theCardsButtonsRunItOnceOrCancelIt() {
        val c = controller()
        c.ask("set a timer for 5 minutes")
        val id = c.state.value.conversation.id
        c.actionConfirmed(id, 1)
        c.actionConfirmed(id, 1)
        until("the card is done") { c.card(1)?.state == CardState.DONE }
        Thread.sleep(50)
        assertEquals("one tap, one timer, however many taps", 1, host.sent.size)
        // A spoken yes to a card already done runs nothing either.
        c.ask("yes")
        assertEquals(1, host.sent.size)
        c.ask("set a timer for 2 minutes")
        val last = c.state.value.conversation.messages.lastIndex
        c.actionDeclined(id, last)
        until("the card is cancelled") { c.card(last)?.state == CardState.DISMISSED }
        assertEquals(1, host.sent.size)
    }

    @Test fun aClockAppThatFailsSaysSo() {
        host.clockWorks = false
        val c = controller()
        c.ask("set a timer for 5 minutes")
        assertEquals(ActionTexts.CLOCK_FAILED, c.ask("yes").text)
        assertEquals(CardState.FAILED, c.card(1)?.state)
    }

    // ── Hidden when no app takes it ─────────────────────────────────────

    @Test fun noClockAppNoCard() {
        host.clockIntents = emptySet()
        val c = controller()
        val t = c.ask("set a timer for 10 minutes")
        assertEquals("This phone has no clock app that can set timers.", t.text)
        assertNull(t.card)
        val a = c.ask("wake me at 7")
        assertEquals("This phone has no clock app that can set alarms.", a.text)
        assertNull(a.card)
        // Showing timers says so, and never reads the house's timers.
        assertEquals("This phone has no clock app that can show its timers.", c.ask("show my timers").text)
        assertTrue(host.sent.isEmpty())
        assertTrue(engines.isEmpty())
    }

    private fun inputs() = PhoneAnswerInputs(ClockStyle(la, Locale.US, false).at(nowMs), ClockStyle(la, Locale.US, false), phone, llm.name, LlmBackend.CPU)


    @Test fun showTheClockAppsTimersAtOnce() {
        val c = controller()
        assertEquals("Opening your timers in the Clock app.", c.ask("show my timers").text)
        assertEquals(ClockRequest(ClockIntent.SHOW_TIMERS, skipUi = false), host.sent.single())
        assertEquals("Opening your alarms in the Clock app.", c.ask("open my alarms").text)
    }

    @Test fun aTimerPastTheClockAppsLimitIsRefused() {
        val c = controller()
        assertEquals(ActionTexts.TIMER_TOO_LONG, c.ask("set a timer for 25 hours").text)
    }

    // ── Done at once, with Undo ─────────────────────────────────────────

    @Test fun pauseRunsAtOnceAndUndoPutsItBack() {
        playing("Glass Harbor", "Blue Train")
        val c = controller()
        val r = c.ask("pause")
        assertEquals("Paused.", r.text)
        assertEquals(CardKind.UNDO, r.card?.kind)
        assertEquals(listOf("pause"), player.calls)
        assertFalse(player.state.playing)
        val u = c.ask("undo")
        assertEquals("Undone: back to \"Glass Harbor\".", u.text)
        assertTrue(player.state.playing)
        assertEquals(CardState.DONE, c.card(1)?.state)
        // Nothing left to undo.
        assertEquals(ActionTexts.NOTHING_TO_UNDO, c.ask("undo").text)
    }

    @Test fun everyPlaybackActionCanBeUndone() {
        val c = controller()
        listOf("pause", "next", "go back", "stop", "set a sleep timer for 20 minutes", "play some jazz", "continue my book").forEach { said ->
            playing("Glass Harbor", "Blue Train", "Clair de Lune", index = 1, pos = 42.0)
            host.songs = listOf(song(7, "Jazz Night"), song(8, "Jazz Morning"))
            host.books = listOf(BookChoice(book(), "Dune", "Frank Herbert", 3725.0, 1.25f, 100))
            val before = player.state
            val r = c.ask(said)
            assertEquals(said, CardKind.UNDO, r.card?.kind)
            assertTrue(said, player.state != before)
            c.ask("undo")
            assertEquals(said, before, player.state)
        }
    }

    private fun book() = PlayItem(
        uid = "saved-k1", kind = PlayKind.Audiobook, id = 9, title = "Dune", artist = "Frank Herbert",
        src = "file:///books/dune.m4b", chapters = listOf(Chapter("One", 0.0), Chapter("Two", 3600.0)), savedKey = "k1",
    )

    @Test fun theUndoButtonWorksAndExpires() {
        playing("Glass Harbor")
        val c = controller()
        c.ask("pause")
        val id = c.state.value.conversation.id
        c.undoTapped(id, 1)
        until("undone") { c.card(1)?.state == CardState.DONE }
        assertTrue(player.state.playing)
        // A minute later "undo" no longer reaches back.
        c.ask("stop")
        nowMs += AssistantController.UNDO_WORD_MS + 1
        assertEquals(ActionTexts.NOTHING_TO_UNDO, c.ask("undo").text)
        assertTrue(player.state.empty)
    }

    @Test fun theUndoChipShowsForTenSeconds() {
        val open = PhoneCard(CardKind.UNDO)
        assertTrue(com.domovoi.app.ui.screens.assistant.undoShowing(open, 1_000, 1_000))
        assertTrue(com.domovoi.app.ui.screens.assistant.undoShowing(open, 1_000, 10_999))
        assertFalse(com.domovoi.app.ui.screens.assistant.undoShowing(open, 1_000, 11_000))
        assertFalse(com.domovoi.app.ui.screens.assistant.undoShowing(open.copy(state = CardState.DONE), 1_000, 2_000))
    }

    @Test fun nothingPlayingOrAlreadyPausedChangesNothing() {
        val c = controller()
        assertEquals(ActionTexts.NOTHING_PLAYING, c.ask("pause").text)
        assertEquals(ActionTexts.NOTHING_PLAYING, c.ask("next").text)
        playing("Glass Harbor", playing = false)
        val r = c.ask("pause")
        assertEquals("It's already paused.", r.text)
        assertNull(r.card)
        assertTrue(player.calls.isEmpty())
    }

    @Test fun whileCastingToARoomTheChatLeavesItAlone() {
        playing("Glass Harbor")
        player.casting = "kitchen"
        phone.serverSet = true
        val c = controller()
        val r = c.ask("pause")
        assertEquals("That's a house thing — ask in a home-server chat.", r.text)
        assertFalse("the room is never named", r.text.contains("kitchen"))
        assertTrue(player.calls.isEmpty())
    }

    @Test fun musicFromThePhone() {
        host.songs = listOf(song(1, "Glass Harbor", "Velvet Kites"), song(2, "Paper Moon", "Velvet Kites"), song(3, "Blue Train", "John Coltrane"))
        val c = controller()
        assertEquals("Playing \"Glass Harbor\" by Velvet Kites, then 1 more.", c.ask("play velvet kites").text)
        assertEquals("play:2@0+0", player.calls.last())
        // A song asked for by name starts the queue; "X by Y" finds it too.
        assertEquals("Playing \"Paper Moon\" by Velvet Kites.", c.ask("I want to hear paper moon by velvet kites").text)
        val all = c.ask("shuffle my music")
        assertTrue(all.text, all.text.endsWith(", then 2 more, shuffled."))
        assertEquals(setOf(1L, 2L, 3L), player.state.queue.map { it.id }.toSet())
        assertEquals("I can't find any music matching \"polka\" on this phone.", c.ask("play polka").text)
        host.songs = null
        assertEquals(ActionTexts.NO_MUSIC_PERMISSION, c.ask("play some jazz").text)
    }

    @Test fun aSavedBookContinuesAtItsPlace() {
        host.books = listOf(
            BookChoice(book(), "Dune", "Frank Herbert", 3725.0, 1.25f, lastPlayedMs = 200),
            BookChoice(book().copy(uid = "saved-k2", title = "The Hobbit"), "The Hobbit", "J. R. R. Tolkien", 0.0, 1f, lastPlayedMs = 100),
        )
        val c = controller()
        assertEquals("Continuing \"Dune\" at 1:02:05.", c.ask("continue my book").text)
        assertEquals("play:1@0+3725", player.calls.last())
        assertEquals(1.25f, player.state.speed)
        assertEquals("Starting \"The Hobbit\".", c.ask("play the hobbit audiobook").text)
        assertEquals("I can't find a saved audiobook matching \"emma\" on this phone.", c.ask("continue listening to emma").text)
        host.books = emptyList()
        assertEquals(ActionTexts.NO_BOOKS, c.ask("continue my book").text)
    }

    @Test fun aVideoAsksFirstThenPlaysOverTheChat() {
        host.videos = listOf(VideoChoice("local:5", "glacier-trip.mp4", "content://media/external/video/media/5"))
        val c = controller()
        val q = c.ask("play the glacier trip video")
        assertEquals("Play \"glacier-trip.mp4\" from this phone's videos?", q.text)
        assertNull(c.state.value.playingVideo)
        c.ask("yes")
        assertEquals("content://media/external/video/media/5", c.state.value.playingVideo?.uri)
        c.videoClosed()
        assertNull(c.state.value.playingVideo)
        host.videos = null
        assertEquals(ActionTexts.NO_VIDEO_PERMISSION, c.ask("play the glacier trip video").text)
    }

    // ── Not this round ──────────────────────────────────────────────────

    @Test fun houseActionsAreOneSentence() {
        phone.serverSet = true
        val c = controller()
        val r = c.ask("play jazz in the kitchen")
        assertEquals("That's a house thing — ask in a home-server chat.", r.text)
        assertNull(r.card)
        assertEquals(ActionTexts.HOUSE, c.ask("cancel the kitchen timer").text)
        assertEquals(ActionTexts.HOUSE, c.ask("set a timer for 10 minutes in the kitchen").text)
        assertTrue(player.calls.isEmpty())
        assertTrue(engines.isEmpty())
    }

    /** Owner, 2026-10-21: phone chats are phone-only. Whatever the phone
     *  context still holds about the house, no action reads its timers or
     *  names its rooms. */
    @Test fun phoneActionsNeverReadHouseTimersOrNameRooms() {
        phone.timers = listOf(PhoneTimer("pizza", reminder = false, triggerAtMs = nowMs + 600_000, room = "larkspur"))
        phone.facts = PromptFacts(rooms = listOf("larkspur", "copper den"), server = ServerLink.CONNECTED)
        phone.serverSet = true
        host.clockIntents = emptySet()
        playing("Glass Harbor")
        val c = controller()
        val said = listOf("show my timers", "show my alarms", "cancel the pizza timer", "play jazz in the larkspur",
            "set a timer for 5 minutes", "pause", "undo").map { c.ask(it).text }
        said.forEach { t ->
            listOf("pizza", "larkspur", "copper den").forEach { assertFalse("\"$t\" names $it", t.contains(it, ignoreCase = true)) }
        }
        player.casting = "larkspur"
        val r = c.ask("next")
        assertEquals(ActionTexts.HOUSE, r.text)
        assertFalse(r.text.contains("larkspur"))
    }

    // ── The model's tools ───────────────────────────────────────────────

    @Test fun e2bIsOfferedNoActionTools() {
        assertFalse(ModelCatalog.gemma4E2b.actionTools)
        assertFalse(ModelCatalog.gemma4E2b.readTools)
        val c = controller()
        c.ask("could you possibly start a little countdown for the eggs")
        val turn = engines.single().turns.single()
        assertNull("no tools on a model without the flag", turn.tools)
        assertTrue(Persona.SYSTEM_PROMPT.contains("If such a request reaches you, it was not done"))
        assertTrue(host.sent.isEmpty())
    }

    @Test fun aModelWithTheFlagOnlyProposesAndTheCardAsks() {
        llm = llm.copy(actionTools = true)
        script = { turn, emit ->
            val tools = turn.tools!!
            assertTrue(tools.names.contains(ActionTools.SET_TIMER))
            val result = tools.run(ActionTools.SET_TIMER, mapOf("minutes" to 4, "label" to "eggs"))
            assertEquals(ActionTools.PROPOSED, result)
            emit("I can set a 4-minute timer for the eggs.")
        }
        val c = controller()
        val r = c.ask("could you possibly start a little countdown for the eggs")
        assertEquals(CardKind.ACTION, r.card?.kind)
        assertEquals(PhoneAction(ActionKind.SET_TIMER, seconds = 240, label = "eggs"), r.card?.action)
        assertTrue("a proposal runs nothing", host.sent.isEmpty())
        c.ask("yes")
        assertEquals(ClockRequest(ClockIntent.SET_TIMER, seconds = 240, label = "eggs"), host.sent.single())
    }

    @Test fun theActionPatternsComeBeforeThePhonesQuestionsButLeaveThemTheirs() {
        val c = controller()
        assertEquals("battery", c.ask("how much battery do i have").fastPath)
        assertEquals("action_set_timer", c.ask("set a timer for 1 minute").fastPath)
        assertNotNull(c.state.value.conversation.messages.last().card)
    }
}
