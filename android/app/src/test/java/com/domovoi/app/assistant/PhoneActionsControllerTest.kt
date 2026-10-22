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
    /** Like ExoPlayer: right after a skip or a restart it reports
     *  playing=false while it seeks, and plays this long after. */
    @Volatile var seekMs: Long = 0

    private fun seeking() {
        if (seekMs <= 0 || !state.playing) return
        state = state.copy(playing = false)
        Thread { Thread.sleep(seekMs); state = state.copy(playing = true) }.start()
    }

    override suspend fun snapshot() = state
    override suspend fun castingTo() = casting
    override suspend fun pause() { calls += "pause"; state = state.copy(playing = false) }
    override suspend fun resume() { calls += "resume"; state = state.copy(playing = true) }
    override suspend fun next() { calls += "next"; state = state.copy(index = (state.index + 1).coerceAtMost(state.queue.lastIndex), positionSec = 0.0); seeking() }
    override suspend fun previous() { calls += "previous"; state = state.copy(index = (state.index - 1).coerceAtLeast(0), positionSec = 0.0); seeking() }
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
    /** Genre name → its songs. */
    var genreMap: Map<String, List<PlayItem>> = emptyMap(),
) : PhoneActionHost {
    val sent = CopyOnWriteArrayList<ClockRequest>()
    /** How long the Clock app takes to answer (a tap and a spoken yes can
     *  then overlap). */
    @Volatile var clockDelayMs: Long = 0
    override fun clockTakes(intent: ClockIntent) = intent in clockIntents
    override suspend fun clock(request: ClockRequest): Boolean {
        sent += request
        if (clockDelayMs > 0) kotlinx.coroutines.delay(clockDelayMs)
        return clockWorks
    }
    override suspend fun songs(query: String, limit: Int): List<PlayItem>? = songs?.filter {
        query.isBlank() || it.title.contains(query, true) || it.artist?.contains(query, true) == true || it.album?.contains(query, true) == true
    }?.take(limit)
    override suspend fun genres(): List<String>? = if (songs == null) null else genreMap.keys.toList()
    override suspend fun genreSongs(name: String, limit: Int): List<PlayItem>? = genreMap[name]?.take(limit)
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
        assertEquals("Sent to the Clock app: a timer for 10 minutes called \"pasta\".", done.text)
        assertEquals(listOf(ClockRequest(ClockIntent.SET_TIMER, seconds = 600, label = "pasta")), host.sent)
        assertEquals(CardState.DONE, c.card(1)?.state)
        assertTrue("no model was loaded", engines.isEmpty())
    }

    /** Fold check 2026-10-21: reminders and "N minutes from now" alarms
     *  are Clock timers, asked first, named for what to do. */
    @Test fun aReminderInMinutesIsANamedTimerCard() {
        val c = controller()
        val q = c.ask("Remind me in five minutes to check Dome of War")
        assertEquals("Set a timer for 5 minutes called \"check Dome of War\" in the Clock app?", q.text)
        assertEquals(CardState.OPEN, q.card?.state)
        c.ask("yes")
        assertEquals(ClockRequest(ClockIntent.SET_TIMER, seconds = 300, label = "check Dome of War"), host.sent.single())
        assertEquals("Set a timer for 5 minutes in the Clock app?", c.ask("Set an alarm for five minutes from now").text)
        val at = c.ask("Remind me at 7 to call the dentist")
        assertEquals("Set an alarm for 7:00 PM today called \"call the dentist\" in the Clock app?", at.text)
        assertTrue("no model was loaded", engines.isEmpty())
    }

    /** Fold check 2026-10-21: "Play Outcast" (not in the library) went to
     *  the model, which said "Playing Outcast now." and nothing played. */
    @Test fun aModelReplyNeverClaimsToHavePlayedSomething() {
        val c = controller()
        script = { _, emit -> emit("Playing Out"); emit("cast now. "); emit("Enjoy the music!") }
        val r = c.ask("Play Outcast")
        assertEquals("${ClaimGuard.PLAY_LINE} Enjoy the music!", r.text)
        assertTrue("nothing played", player.calls.isEmpty())
        script = { _, emit -> emit("OutKast is a hip hop duo from Atlanta.") }
        assertEquals("OutKast is a hip hop duo from Atlanta.", c.ask("who are outkast").text)
    }

    /** Fold check 2026-10-21: he meant OutKast. A near spelling is asked
     *  about; yes plays it, no does nothing; a guess never plays by itself. */
    @Test fun aNearSpellingIsAskedAndOnlyAYesPlaysIt() {
        host.songs = listOf(song(1, "Hey Ya!", "OutKast"), song(2, "Roses", "OutKast"), song(3, "Let It Be", "The Beatles"))
        val c = controller()
        val q = c.ask("Play Outcast")
        assertEquals("Did you mean OutKast?", q.text)
        assertEquals(CardKind.ACTION, q.card?.kind)
        assertTrue("nothing played on a guess", player.calls.isEmpty())
        assertTrue("no model", engines.isEmpty())
        val yes = c.ask("yes")
        assertTrue(yes.text, yes.text.startsWith("Playing \"Hey Ya!\" by OutKast"))
        assertEquals(listOf("play:2@0+0"), player.calls.toList())
        assertEquals(CardState.DONE, c.card(1)?.state)
        // No: nothing.
        player.calls.clear()
        c.ask("play the beatls")
        assertEquals(ActionTexts.DECLINED, c.ask("no").text)
        assertTrue(player.calls.isEmpty())
        // The button.
        c.ask("play outcast")
        val i = c.state.value.conversation.messages.lastIndex
        c.actionConfirmed(c.state.value.conversation.id, i)
        until("played by the button") { player.calls.isNotEmpty() }
        until("the card is done") { c.card(i)?.state == CardState.DONE }
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
        assertEquals("Sent to the Clock app: an alarm for 7:00 AM.", heard.joinToString(""))
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
            host.genreMap = mapOf("Jazz" to host.songs!!)
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
        assertEquals(PhoneQuestions.HOUSE_THINGS, r.text)
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
        // Nothing in the library by that name: not an action, the model's.
        assertNull(c.ask("play polka").fastPath)
        host.songs = null
        assertEquals(ActionTexts.NO_MUSIC_PERMISSION, c.ask("play some music by velvet kites").text)
        assertNull("no permission, no music word: the model's", c.ask("put on my shoes").fastPath)
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
        assertEquals(PhoneQuestions.HOUSE_THINGS, r.text)
        assertNull(r.card)
        assertEquals(PhoneQuestions.HOUSE_THINGS, c.ask("cancel the kitchen timer").text)
        assertEquals(PhoneQuestions.HOUSE_THINGS, c.ask("set a timer for 10 minutes in the kitchen").text)
        assertTrue(player.calls.isEmpty())
        assertTrue(engines.isEmpty())
    }

    /** Owner, 2026-10-21: phone chats are phone-only: no action reads the
     *  house's timers or names its rooms, even a room the phone casts to. */
    @Test fun phoneActionsNeverReadHouseTimersOrNameRooms() {
        // The phone context has no house timers or room names to give
        // (wf/phone-facts, owner: strict); the replies name none either.
        phone.facts = PromptFacts(server = ServerLink.CONNECTED)
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
        assertEquals(PhoneQuestions.HOUSE_THINGS, r.text)
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

    // ── Review round 2 (wf/phone-actions) ───────────────────────────────

    @Test fun undoWhileCastingToARoomTouchesNothing() {
        playing("Glass Harbor")
        val c = controller()
        val paused = c.ask("pause")
        player.casting = "kitchen" // cast from the Music screen
        player.calls.clear()
        val r = c.ask("undo")
        assertEquals(PhoneQuestions.HOUSE_THINGS, r.text)
        assertEquals(AssistantController.FAST_ACTION_HOUSE, r.fastPath)
        assertTrue("nothing reaches the room: ${player.calls}", player.calls.isEmpty())
        // The button too.
        val i = c.state.value.conversation.messages.indexOf(paused)
        c.undoTapped(c.state.value.conversation.id, i)
        Thread.sleep(300)
        assertTrue("the button reaches nothing either: ${player.calls}", player.calls.isEmpty())
        assertEquals(CardState.OPEN, c.card(i)?.state)
    }

    @Test fun aTapThenASpokenYesSendOneClockRequestAndTheCardStaysDone() {
        val c = controller()
        c.ask("set a timer for 10 minutes")
        val id = c.state.value.conversation.id
        host.clockDelayMs = 300
        c.actionConfirmed(id, 1)
        until("the tap reached the Clock app") { host.sent.size == 1 }
        val n = c.state.value.conversation.messages.size
        c.send("yes")
        until("the reply to yes", 20_000) { !c.state.value.replying && c.state.value.conversation.messages.size >= n + 2 }
        assertEquals(CardState.DONE, c.card(1)?.state)
        c.actionConfirmed(id, 1)
        Thread.sleep(500)
        assertEquals("exactly one request", 1, host.sent.size)
        assertEquals(CardState.DONE, conversations.load(id)!!.messages[1].card?.state)
    }

    @Test fun aSpokenYesThenATapSendOneClockRequest() {
        val c = controller()
        c.ask("set a timer for 10 minutes")
        val id = c.state.value.conversation.id
        host.clockDelayMs = 300
        val n = c.state.value.conversation.messages.size
        c.send("yes")
        until("the yes reached the Clock app") { host.sent.size == 1 }
        c.actionConfirmed(id, 1)
        until("the reply to yes", 20_000) { !c.state.value.replying && c.state.value.conversation.messages.size >= n + 2 }
        Thread.sleep(500)
        assertEquals("exactly one request", 1, host.sent.size)
        assertEquals(CardState.DONE, c.card(1)?.state)
    }

    @Test fun undoAfterATimerHasNothingToUndo() {
        playing("Glass Harbor")
        val c = controller()
        c.ask("pause")
        c.ask("set a timer for 5 minutes")
        c.ask("yes")
        player.calls.clear()
        assertEquals(ActionTexts.NOTHING_TO_UNDO, c.ask("undo").text)
        assertTrue(player.calls.isEmpty())
        assertFalse(player.state.playing)
        // An alarm's question alone ends it too.
        c.ask("next")
        c.ask("set an alarm for 7 am")
        c.ask("no")
        assertEquals(ActionTexts.NOTHING_TO_UNDO, c.ask("undo").text)
        assertFalse(player.calls.contains("restore"))
    }

    @Test fun undoAfterThePersonChangedThePlayerIsRefused() {
        playing("Glass Harbor", "Paper Moon")
        val c = controller()
        c.ask("pause")
        val theirs = PlayerSnapshot(listOf(song(9, "A Whole New Album")), 0, 5.0, playing = true, sleepSec = null)
        player.state = theirs
        val r = c.ask("undo")
        assertEquals(ActionTexts.UNDO_CHANGED, r.text)
        assertFalse(player.calls.contains("restore"))
        assertEquals(theirs, player.state)
    }

    @Test fun bareContinueAfterAStoryIsTheModels() {
        playing("Glass Harbor", "Paper Moon", playing = false)
        host.songs = listOf(song(3, "Shake It Off"), song(4, "Let It Be"))
        script = { _, emit -> emit("Once upon a time, a fox") }
        val c = controller()
        c.ask("tell me a story about a fox")
        for (said in listOf("continue", "next one", "keep going", "let's hear it", "can I hear it", "let's hear a joke")) {
            val r = c.ask(said)
            assertNull("$said: ${r.text}", r.fastPath)
        }
        assertTrue("the player was never touched: ${player.calls}", player.calls.isEmpty())
    }

    @Test fun bareContinueIsPlaybacksAfterAPlaybackTurnOrWhilePlaying() {
        playing("Glass Harbor", "Paper Moon")
        val c = controller()
        c.ask("pause")
        assertEquals("action_resume", c.ask("continue").fastPath)
        assertEquals("action_next", c.ask("next one").fastPath)
        // A fresh chat while music plays: "next one" skips.
        val d = controller()
        d.newConversation()
        until("a new chat") { d.state.value.conversation.messages.isEmpty() }
        player.state = player.state.copy(index = 0, playing = true)
        assertEquals("action_next", d.ask("next one").fastPath)
    }

    @Test fun aDoneTimerCardOpensTheClockAppsTimers() {
        val c = controller()
        c.ask("set a timer for 10 minutes")
        assertFalse("not before it's done", c.clockOpens(c.card(1)!!))
        c.ask("yes")
        val done = c.card(1)!!
        assertTrue(c.clockOpens(done))
        c.openClock(done)
        until("the Clock app's timers") { host.sent.size == 2 }
        assertEquals(ClockRequest(ClockIntent.SHOW_TIMERS, skipUi = false), host.sent.last())
        host.clockIntents = setOf(ClockIntent.SET_TIMER)
        assertFalse("no button when nothing opens them", c.clockOpens(done))
    }

    // ── Review round 2 ──────────────────────────────────────────────────

    private fun timerCardTapped(): Pair<AssistantController, String> {
        val c = controller()
        c.ask("set a timer for 10 minutes")
        val id = c.state.value.conversation.id
        host.clockDelayMs = 300
        c.actionConfirmed(id, 1)
        until("the tap is in the Clock app call") { host.sent.size == 1 }
        return c to id
    }

    private fun diskCard(id: String, i: Int) = conversations.load(id)!!.messages[i].card?.state

    @Test fun aTapThenAModelQuestionKeepsTheCardTaken() {
        script = { _, emit -> kotlinx.coroutines.delay(600); emit("Rome is the capital of Italy.") }
        val (c, id) = timerCardTapped()
        assertEquals(CardState.SENDING, diskCard(id, 1))
        c.ask("what is the capital of italy and why is it famous")
        until("the tap's DONE") { c.card(1)?.state == CardState.DONE }
        Thread.sleep(300)
        assertEquals(CardState.DONE, c.card(1)?.state)
        assertEquals(CardState.DONE, diskCard(id, 1))
        host.clockDelayMs = 0
        c.actionConfirmed(id, 1)
        Thread.sleep(400)
        assertEquals("exactly one request", 1, host.sent.size)
    }

    @Test fun aTapThenHmmIsAlreadyAnswered() {
        val (c, _) = timerCardTapped()
        val r = c.ask("hmm")
        assertEquals(ActionTexts.ALREADY_ANSWERED, r.text)
        assertNull("no second card", r.card)
        assertEquals(CardState.DONE, c.card(1)?.state)
        host.clockDelayMs = 0
        c.ask("yes")
        assertEquals("exactly one request", 1, host.sent.size)
    }

    @Test fun aTapThenNoIsAlreadyAnswered() {
        val (c, id) = timerCardTapped()
        val r = c.ask("no")
        assertEquals(ActionTexts.ALREADY_ANSWERED, r.text)
        assertEquals(CardState.DONE, c.card(1)?.state)
        assertEquals(CardState.DONE, diskCard(id, 1))
        assertEquals(1, host.sent.size)
    }

    @Test fun aTapThenANewChatKeepsTheCardDoneOnDisk() {
        val (c, id) = timerCardTapped()
        c.newConversation()
        until("a new chat") { c.state.value.conversation.id != id }
        until("the tap's DONE on disk") { diskCard(id, 1) == CardState.DONE }
        c.open(id)
        until("reopened") { c.state.value.conversation.id == id }
        assertEquals(CardState.DONE, c.card(1)?.state)
        host.clockDelayMs = 0
        c.actionConfirmed(id, 1)
        Thread.sleep(400)
        assertEquals("exactly one request", 1, host.sent.size)
    }

    @Test fun nextOneAfterAJokeIsTheModelsEvenWhileMusicPlays() {
        playing("Glass Harbor", "Paper Moon")
        script = { _, emit -> emit("Why did the fox cross the road?") }
        val c = controller()
        c.ask("tell me a joke")
        assertNull(c.ask("next one").fastPath)
        c.ask("tell me a story")
        assertNull(c.ask("continue").fastPath)
        assertNull(c.ask("keep going").fastPath)
        assertTrue("the player was never touched: ${player.calls}", player.calls.isEmpty())
        // After a playback turn, "next one" is the player's again.
        c.ask("pause")
        assertEquals("action_next", c.ask("next one").fastPath)
    }

    @Test fun aModelTurnEndsTheUndo() {
        playing("Glass Harbor")
        val c = controller()
        c.ask("pause")
        c.ask("tell me a joke")
        assertEquals(ActionTexts.NOTHING_TO_UNDO, c.ask("undo").text)
        c.ask("play")
        c.ask("pause")
        c.ask("what time is it")
        assertEquals(ActionTexts.NOTHING_TO_UNDO, c.ask("undo").text)
        assertFalse(player.calls.contains("restore"))
    }

    @Test fun undoAfterThePersonResumedItThemselvesIsRefused() {
        playing("A", "B", "C")
        val c = controller()
        c.ask("pause")
        player.state = player.state.copy(playing = true, positionSec = 50.0)
        assertEquals(ActionTexts.UNDO_CHANGED, c.ask("undo").text)
        assertFalse(player.calls.contains("restore"))
    }

    /** The A/B's real requests (review round 2, ab-probe.txt), each in the library. */
    private val realTitles = listOf(
        "Sweater Weather" to "play Sweater Weather", "Deck the Halls" to "play Deck the Halls", "Your Song" to "play Your Song",
        "Piano Man" to "play Piano Man", "Story of My Life" to "play Story of My Life", "To Build a Home" to "play To Build a Home",
        "The Smile" to "play The Smile", "Smile" to "play Smile by Lily Allen", "Jumper" to "play Jumper",
        "Fool in the Rain" to "play Fool in the Rain", "Guitar Man" to "play Guitar Man", "Harmonica Blues" to "play Harmonica Blues",
        "Coat of Many Colors" to "play Coat of Many Colors", "Your Love Is My Drug" to "play Your Love Is My Drug",
        "His Eyes" to "play His Eyes", "Their Way" to "play Their Way", "To Zion" to "play To Zion",
        "Trick of the Light" to "play Trick of the Light", "Movies" to "play Movies by Weezer", "Stories" to "play Stories",
        "Jokes" to "play Jokes", "Film" to "play Film", "Hat" to "play Hat", "Drums of Death" to "play Drums of Death",
        "Scarf" to "play Scarf", "Prank" to "play Prank", "Harmonica Song" to "play the harmonica song",
        "Sweater Weather" to "put on Sweater Weather", "Your Song" to "listen to Your Song", "Piano Man" to "let me hear Piano Man",
        "One" to "play One", "Please Please Me" to "play Please Please Me", "Here" to "play Here", "New" to "play New",
        "Long Game" to "play Long Game", "Some Nights" to "play Some Nights", "My Way" to "play My Way",
        "That's Life" to "play That's Life", "New Rules" to "play New Rules",
    )

    private fun library(): List<PlayItem> = realTitles.map { it.first }.distinct().mapIndexed { i, t ->
        when (t) {
            "Smile" -> song(100L + i, t, "Lily Allen")
            "Movies" -> song(100L + i, t, "Weezer")
            else -> song(100L + i, t)
        }
    } + listOf(song(90, "Lone Rider", "Video Age"))

    @Test fun realSongTitlesInTheLibraryPlay() {
        host.songs = library()
        val c = controller()
        val missed = realTitles.filter { (title, said) ->
            val r = c.ask(said)
            r.fastPath != "action_play_music" || player.state.current?.title != title
        }
        assertEquals("every title plays: $missed", emptyList<Pair<String, String>>(), missed)
        assertEquals("action_play_music", c.ask("play songs by Video Age").fastPath)
    }

    /** Review round 2's near-misses that still played (review-r2-probe.txt). */
    private val stillPlayed = listOf(
        "play tic tac toe", "play by the rules", "play word games with me", "play the part of a pirate", "let's hear the next one",
        "put on some coffee", "put on a happy face", "put on my shoes", "play the next chapter of the story", "listen to the kids",
        "put on a play", "play a song for me in your head", "put on a podcast voice",
    )

    @Test fun nearMissesNeverPlayEvenWithPlayAndHomeInTheLibrary() {
        host.songs = listOf(
            song(1, "Play That Funky Music", "Wild Cherry"), song(2, "Games People Play"), song(3, "Home", "Michael Bublé"),
            song(4, "To Build a Home", "The Cinematic Orchestra"), song(5, "Coffee Shop Blues"), song(6, "New Rules"),
            song(7, "Kids in America"), song(8, "Happy"),
        )
        host.genreMap = mapOf("Pop" to host.songs!!.take(2), "Jazz" to listOf(song(9, "Blue in Green")))
        script = { _, emit -> emit("Sure.") }
        val c = controller()
        for (said in stillPlayed) {
            val r = c.ask(said)
            assertNull("$said -> ${r.text}", r.fastPath)
        }
        assertTrue("nothing played: ${player.calls}", player.calls.isEmpty())
        // The library still answers real requests, a genre among them.
        assertEquals("Playing \"Blue in Green\".", c.ask("play some jazz").text)
        assertEquals("action_play_music", c.ask("play home").fastPath)
    }

    // ── Batch 23 emulator gate: "go back", then "undo" ─────────────────

    @Test fun undoWorksWhenThePlayerWasStillSeekingRightAfterTheAction() {
        playing("Glass Harbor", "Paper Moon", "Blue Train", index = 1, pos = 42.0)
        player.seekMs = 150
        val c = controller()
        for (said in listOf("go back", "next", "skip")) {
            val before = player.state
            c.ask(said)
            Thread.sleep(400) // it plays again a moment later
            assertTrue(said, player.state.playing)
            val r = c.ask("undo")
            assertTrue("$said: ${r.text}", r.text.startsWith("Undone"))
            assertEquals(said, before, player.state)
        }
        // A pause undone after it is still paused, and a person's own resume
        // is still refused.
        c.ask("pause")
        assertTrue(c.ask("undo").text.startsWith("Undone"))
        c.ask("pause")
        player.state = player.state.copy(playing = true)
        assertEquals(ActionTexts.UNDO_CHANGED, c.ask("undo").text)
    }
}
