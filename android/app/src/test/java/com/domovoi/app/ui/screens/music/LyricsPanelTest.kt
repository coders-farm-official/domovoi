package com.domovoi.app.ui.screens.music

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.lazy.LazyItemScope
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import com.domovoi.app.AppContainer
import com.domovoi.app.LocalApp
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.StateBus
import com.domovoi.app.net.WsEvent
import com.domovoi.app.net.decode
import com.domovoi.app.player.LyricsMath
import com.domovoi.app.player.LyricsRepository
import com.domovoi.app.player.LyricsResult
import com.domovoi.app.player.PlayItem
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.player.RemoteNowPlaying
import com.domovoi.app.testing.HeadlessUi
import com.domovoi.app.testing.JvmComposition
import com.domovoi.app.testing.NodeTree
import com.domovoi.app.testing.allocateWithoutConstructor
import com.domovoi.app.testing.appWith
import com.domovoi.app.testing.button
import com.domovoi.app.testing.buttons
import com.domovoi.app.testing.composeTest
import com.domovoi.app.testing.setField
import com.domovoi.app.testing.testPlayer
import com.domovoi.app.testing.textButton
import com.domovoi.app.testing.texts
import com.domovoi.app.testing.withHeadlessWindow
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/**
 * The lyrics surfaces of the Android players (lyrics-build CONTRACT
 * [D5]–[D8], [D12]), composed for real on the JVM against a fake web:
 *
 *  - a position tick (this phone's 500 ms tick, or a room's 2 s poll)
 *    recomposes the lyrics alone — never the player tab around them — and
 *    the lyrics only when the line being sung changes (the
 *    PlayerTabRecompositionTest precedent);
 *  - the "lyrics" item sits right after the player head, for library
 *    tracks only;
 *  - each state says what it should, a refusal shows nothing at all, and
 *    the room's timing nudge is per room;
 *  - a room card shows the room's current timed line.
 *
 * Every lyric here is invented.
 */
class LyricsPanelTest {
    private lateinit var server: MockWebServer

    @Before fun up() {
        server = MockWebServer().also { it.start() }
    }

    @After fun down() = server.shutdown()

    @Suppress("UNCHECKED_CAST")
    private fun <T> writable(flow: StateFlow<T>) = flow as MutableStateFlow<T>

    private val lantern = PlayItem.fromTrack(12, "Lantern Song", "The Example Band", null, 205.0)

    private fun docJson(
        id: Long,
        status: String = "synced",
        checking: Boolean = false,
        label: String? = "from Lantern Song.lrc",
        text: String? = "the lantern hums beside the river door\nand every copper kettle sings at dawn\nwe carried paper boats along the hall",
    ): String {
        val lines = if (status == "synced") {
            """[{"t": 12400, "text": "the lantern hums beside the river door"},
                {"t": 16850, "text": "and every copper kettle sings at dawn"},
                {"t": 21100, "text": ""},
                {"t": 21300, "text": "we carried paper boats along the hall"}]"""
        } else {
            "null"
        }
        val labelJson = label?.let { "\"$it\"" } ?: "null"
        val textJson = text?.let { JsonPrimitive(it).toString() } ?: "null"
        return """{"track_id": $id, "status": "$status", "checking": $checking, "source": "sidecar",
                   "source_label": $labelJson, "lines": $lines, "text": $textJson}"""
    }

    private fun ok(body: String) = MockResponse().setBody(body).setHeader("Content-Type", "application/json")

    /**
     * A real player and lyrics repository over the fake web, in an app graph
     * holding just what the lyrics surfaces read: the repository, the four
     * Prefs flows they collect, and the state bus.
     */
    private inner class Rig {
        val player = testPlayer()
        val base = server.url("/").toString().trimEnd('/')
        val repo = LyricsRepository(ApiClient(baseUrlProvider = { base }), clock = { 0L })
        val nudges = MutableStateFlow<Map<String, Long>>(emptyMap())
        val events = MutableSharedFlow<WsEvent>(extraBufferCapacity = 16)
        val prefs: Prefs = allocateWithoutConstructor(Prefs::class.java).also { prefs ->
            listOf(
                "serverUrl" to MutableStateFlow(base),
                "lyricsPanelOpen" to MutableStateFlow(true),
                "lyricsSheetOpen" to MutableStateFlow(false),
                "lyricsRoomNudge" to nudges,
            ).forEach { (name, flow) ->
                setField(prefs, "_$name", flow)
                setField(prefs, name, flow)
            }
            // The setters save in the background; there is no DataStore here.
            setField(prefs, "scope", CoroutineScope(SupervisorJob() + Dispatchers.Unconfined + CoroutineExceptionHandler { _, _ -> }))
        }
        val app: AppContainer = appWith(player).also { app ->
            setField(app, "lyrics", repo)
            setField(app, "prefs", prefs)
            val bus = allocateWithoutConstructor(StateBus::class.java)
            setField(bus, "_events", events)
            setField(bus, "events", events)
            setField(app, "bus", bus)
        }

        /** Put [json] in the repository's cache, the way a first fetch does. */
        suspend fun prime(id: Long, json: String) {
            server.enqueue(ok(json))
            assertTrue(repo.forTrack(id) is LyricsResult.Loaded)
        }
    }

    /** Stands in for the lyrics panel's line list: the panel's own load and
     *  position, and the line it would highlight. [onRun] hears every run. */
    @Composable
    private fun LyricsProbe(trackId: Long, follow: LyricsFollow, onRun: (Int) -> Unit) {
        val load = rememberLyricsLoad(trackId, (follow as? LyricsFollow.Room)?.roomId)
        val lines = (load.result as? LyricsResult.Loaded)?.doc?.timedLines().orEmpty()
        val position = rememberLyricsPosition(load, follow)
        val active by remember(lines, position) {
            derivedStateOf { LyricsMath.activeIndex(lines, position.ms.longValue) }
        }
        onRun(active)
    }

    /** The music page's caller of the tab model, with the lyrics under it. */
    private class Counts {
        var tab = 0
        var panel = 0
        var active = -9
        var model: PlayerTabModel? = null
    }

    @Composable
    private fun TabWithLyrics(app: AppContainer, counts: Counts) {
        CompositionLocalProvider(LocalApp provides app) {
            counts.tab++
            val model = rememberPlayerTabModel()
            counts.model = model
            model.lyricsTrackId?.let { id ->
                LyricsProbe(id, model.lyricsFollow) { i ->
                    counts.panel++
                    counts.active = i
                }
            }
        }
    }

    private suspend fun JvmComposition.settleUntil(what: String, ok: () -> Boolean) {
        repeat(150) {
            settle(untilQuiet = false, frames = 4)
            if (ok()) return
            delay(20)
        }
        fail("never: $what")
    }

    // ── recomposition ───────────────────────────────────────────────────

    @Test fun aPositionTickRecomposesTheLyricsAloneNotTheTab() = composeTest {
        val rig = Rig()
        rig.prime(12, docJson(12))
        writable(rig.player.queue).value = listOf(lantern)
        val counts = Counts()
        setContent { TabWithLyrics(rig.app, counts) }
        settle()
        assertEquals(12L, counts.model!!.lyricsTrackId)
        assertEquals(LyricsFollow.Local, counts.model!!.lyricsFollow)
        assertEquals(-1, counts.active)
        val tab = counts.tab
        val panel = counts.panel

        val position = writable(rig.player.positionSec)
        val seen = mutableListOf<Int>()
        for (sec in listOf(0.5, 1.0, 12.0, 12.25, 12.5, 13.0, 16.5, 16.7, 17.2, 21.0, 21.5)) {
            position.value = sec
            settle()
            seen += counts.active
        }

        assertEquals("a position tick recomposed the player tab", tab, counts.tab)
        // Each line shows LEAD_MS early; the gap at 21.1 s is a line too.
        assertEquals(listOf(-1, -1, -1, 0, 0, 0, 0, 1, 1, 2, 3), seen)
        // Eleven ticks, four new lines: the lyrics ran four times.
        assertEquals(panel + 4, counts.panel)
    }

    @Test fun aRoomPollMovesTheLyricsAloneAndOnlyANewSongRebuildsTheTab() = composeTest {
        val rig = Rig()
        rig.prime(12, docJson(12))
        writable(rig.player.target).value = PlayTarget.Room("kitchen")
        val remote = writable(rig.player.remote)
        fun reading(sec: Double, at: Long, track: Long? = 12) = RemoteNowPlaying(
            "kitchen", "pause", "Lantern Song", "The Example Band", sec, 205.0, trackId = track, readAtMs = at,
        )
        remote.value = reading(10.0, 1_000)
        val counts = Counts()
        setContent { TabWithLyrics(rig.app, counts) }
        settle()
        assertEquals(12L, counts.model!!.lyricsTrackId)
        assertEquals(LyricsFollow.Room("kitchen"), counts.model!!.lyricsFollow)
        assertEquals(-1, counts.active)
        val tab = counts.tab

        remote.value = reading(12.4, 3_000)
        settle()
        assertEquals(0, counts.active)
        remote.value = reading(14.0, 5_000)
        settle()
        assertEquals(0, counts.active)
        // The kitchen's nudge (lyrics 1.8 s later) puts the line back...
        rig.nudges.value = mapOf("kitchen" to 1_800L)
        settle()
        assertEquals(-1, counts.active)
        // ...and another room's nudge is not the kitchen's.
        rig.nudges.value = mapOf("office" to 1_800L)
        settle()
        assertEquals(0, counts.active)
        remote.value = reading(17.0, 7_000)
        settle()
        assertEquals(1, counts.active)
        assertEquals("a room poll recomposed the player tab", tab, counts.tab)

        // The room moved on to another song: that one the tab must know.
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"room kitchen is not provisioned"}"""))
        server.enqueue(ok(docJson(13)))
        remote.value = reading(0.0, 9_000, track = 13)
        settle()
        assertTrue(counts.tab > tab)
        assertEquals(13L, counts.model!!.lyricsTrackId)
    }

    // ── the player tab's items ──────────────────────────────────────────

    /** Records the keys a tab hands its LazyColumn, composing nothing. */
    private class RecordingScope : LazyListScope {
        val keys = mutableListOf<Any?>()

        override fun item(key: Any?, contentType: Any?, content: @Composable LazyItemScope.() -> Unit) {
            keys += key
        }

        override fun items(
            count: Int,
            key: ((index: Int) -> Any)?,
            contentType: (index: Int) -> Any?,
            itemContent: @Composable LazyItemScope.(index: Int) -> Unit,
        ) {
            repeat(count) { keys += key?.invoke(it) }
        }

        @ExperimentalFoundationApi
        override fun stickyHeader(key: Any?, contentType: Any?, content: @Composable LazyItemScope.() -> Unit) {
            item(key, contentType, content)
        }
    }

    private fun keys(queue: List<PlayItem>, room: PlayTarget.Room? = null, lyrics: Long? = null): List<Any?> {
        val model = PlayerTabModel(queue, 0, room, emptyList(), mutableIntStateOf(0), queue.isNotEmpty(), lyrics)
        return RecordingScope().also { it.playerTab(model, listOf("kitchen"), onSaveQueue = {}) }.keys
    }

    @Test fun theLyricsItemComesRightAfterTheHeadWhenThereIsATrack() {
        assertEquals(
            listOf("player-head", "player-lyrics", "player-queue-label", playerQueueKey(0, lantern)),
            keys(listOf(lantern), lyrics = 12),
        )
        // Casting with an empty phone queue: the room's song has lyrics too.
        assertEquals(
            listOf("player-head", "player-lyrics", "player-queue-label", "player-queue-empty"),
            keys(emptyList(), PlayTarget.Room("kitchen"), lyrics = 12),
        )
        val station = PlayItem.fromStation(3, "Harbor FM")
        assertEquals(listOf("player-head", "player-queue-label", playerQueueKey(0, station)), keys(listOf(station)))
    }

    @Test fun onlyALibraryTrackHasLyrics() {
        assertEquals(12L, lyricsTrackIdOf(lantern))
        assertNull(lyricsTrackIdOf(null))
        assertNull(lyricsTrackIdOf(PlayItem.fromStation(3, "Harbor FM")))
        assertNull(lyricsTrackIdOf(PlayItem.fromEpisode(4, "Episode", "Show", 60.0, null, emptyList())))
        assertNull(lyricsTrackIdOf(PlayItem.fromBook(5, "The Long Book", "someone", 9_000.0, null, emptyList())))
        assertNull(lyricsTrackIdOf(PlayItem.fromDeviceAudio(6, "Pocket Tune", "me", null, 120.0, "content://x/6", null)))
    }

    @Test fun theModelTakesTheRoomsTrackWhileCastingAndNoneForARadioStation() = composeTest {
        val rig = Rig()
        writable(rig.player.queue).value = listOf(PlayItem.fromStation(3, "Harbor FM"))
        val counts = Counts()
        setContent { TabWithLyrics(rig.app, counts) }
        settle()
        assertNull(counts.model!!.lyricsTrackId)

        // The phone's queue still says radio; the room plays track 12.
        rig.prime(12, docJson(12))
        writable(rig.player.remote).value = RemoteNowPlaying(
            "kitchen", "play", "Lantern Song", "The Example Band", 1.0, 205.0, trackId = 12, readAtMs = 0,
        )
        writable(rig.player.target).value = PlayTarget.Room("kitchen")
        settle()
        assertEquals(12L, counts.model!!.lyricsTrackId)
        // A reading of another room says nothing about this one.
        writable(rig.player.remote).value = RemoteNowPlaying(
            "office", "play", "Glass Harbor", "The Velvet Kites", 1.0, 180.0, trackId = 40, readAtMs = 0,
        )
        settle()
        assertNull(counts.model!!.lyricsTrackId)
    }

    // ── the section and its states ──────────────────────────────────────

    private fun sectionTree(
        rig: Rig,
        trackId: Long = 12,
        follow: LyricsFollow = LyricsFollow.Local,
        open: Boolean = true,
        onOpen: (Boolean) -> Unit = {},
        check: suspend JvmComposition.(NodeTree) -> Unit,
    ) {
        val tree = NodeTree()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(LocalApp provides rig.app) {
                            LyricsSection(trackId, follow, PLAYER_LYRICS_HEIGHT, open, onOpen)
                        }
                    }
                }
                settle(untilQuiet = false, frames = 10)
                check(tree)
            }
        }
    }

    @Test fun theHeaderOpensAndClosesTheSection() {
        val rig = Rig()
        kotlinx.coroutines.runBlocking { rig.prime(12, docJson(12)) }
        val asked = mutableListOf<Boolean>()
        sectionTree(rig, open = true, onOpen = { asked += it }) { tree ->
            assertTrue(tree.texts().contains("lyrics"))
            // Open: the panel, its source under the lines.
            assertTrue(tree.texts().toString(), tree.texts().contains("from Lantern Song.lrc"))
            tree.button("hide lyrics").click()
        }
        sectionTree(rig, open = false, onOpen = { asked += it }) { tree ->
            assertFalse(tree.texts().contains("from Lantern Song.lrc"))
            tree.button("show lyrics").click()
        }
        assertEquals(listOf(false, true), asked)
    }

    @Test fun eachStateSaysWhatItIs() {
        val rig = Rig()
        kotlinx.coroutines.runBlocking {
            rig.prime(21, docJson(21, status = "plain", label = "from the song file", text = "the river door is open tonight\n\nwe carried paper boats along the hall"))
            rig.prime(22, docJson(22, status = "instrumental", label = "from LRCLIB", text = null))
            rig.prime(23, docJson(23, status = "none", checking = true, label = null, text = null))
            rig.prime(24, docJson(24, status = "none", label = null, text = null))
        }
        sectionTree(rig, trackId = 21) { tree ->
            assertTrue(tree.texts().contains("the river door is open tonight\n\nwe carried paper boats along the hall"))
            assertTrue(tree.texts().contains("from the song file"))
        }
        sectionTree(rig, trackId = 22) { tree ->
            assertTrue(tree.texts().contains("instrumental — no words to show"))
            assertTrue(tree.texts().contains("from LRCLIB"))
        }
        sectionTree(rig, trackId = 23) { tree -> assertTrue(tree.texts().contains("looking for lyrics…")) }
        sectionTree(rig, trackId = 24) { tree -> assertTrue(tree.texts().contains("no lyrics for this song")) }
    }

    @Test fun aFailureSaysSoAndRetries() {
        val rig = Rig()
        server.enqueue(MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}"""))
        server.enqueue(ok(docJson(12)))
        sectionTree(rig) { tree ->
            settleUntil("the failure shows") { tree.texts().contains("couldn't load the lyrics") }
            tree.textButton("retry").click()
            settleUntil("the retry loads the lyrics") { tree.texts().contains("from Lantern Song.lrc") }
            assertFalse(tree.texts().contains("couldn't load the lyrics"))
        }
        assertEquals(2, server.requestCount)
    }

    @Test fun aViewerOutsideTheHouseholdSeesNothingAtAll() {
        val rig = Rig()
        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"household device token required"}"""))
        sectionTree(rig) { tree ->
            settleUntil("the refusal is in") { server.requestCount == 1 && tree.buttons().isEmpty() }
            settle(untilQuiet = false, frames = 10)
            assertEquals(emptyList<String>(), tree.texts())
            assertEquals(0, tree.buttons().size)
        }
    }

    @Test fun aRoomsTimingNudgeIsKeptPerRoom() {
        val rig = Rig()
        kotlinx.coroutines.runBlocking { rig.prime(12, docJson(12)) }
        writable(rig.player.target).value = PlayTarget.Room("kitchen")
        writable(rig.player.remote).value = RemoteNowPlaying(
            "kitchen", "pause", "Lantern Song", "The Example Band", 13.0, 205.0, trackId = 12, readAtMs = 0,
        )
        sectionTree(rig, follow = LyricsFollow.Room("kitchen")) { tree ->
            assertTrue(tree.texts().contains("timing"))
            assertFalse("nothing to reset yet", tree.button("reset lyrics timing").enabled)
            tree.button("lyrics later").click()
            settle(untilQuiet = false, frames = 10)
            tree.button("lyrics later").click()
            settle(untilQuiet = false, frames = 10)
            assertEquals(mapOf("kitchen" to 500L), rig.nudges.value)
            assertTrue(tree.texts().toString(), tree.texts().contains("timing +0.5 s"))
            tree.button("lyrics earlier").click()
            settle(untilQuiet = false, frames = 10)
            assertEquals(mapOf("kitchen" to 250L), rig.nudges.value)
            tree.button("reset lyrics timing").click()
            settle(untilQuiet = false, frames = 10)
            assertEquals(emptyMap<String, Long>(), rig.nudges.value)
        }
        // This phone's own playback has no nudge.
        sectionTree(rig, follow = LyricsFollow.Local) { tree ->
            assertTrue(tree.buttons().none { it.label == "lyrics later" })
            assertFalse(tree.texts().contains("timing"))
        }
    }

    /**
     * Nothing is ever laid out here, so every panel is gone before its first
     * layout. LazyListState.scrollToItem / animateScrollToItem wait for that
     * layout in a suspension that cannot be cancelled: a panel that used
     * them then kept its coroutine for good, and this test never ended (as
     * the first run of this suite did). The timeout turns that into a failure.
     */
    @Test(timeout = 60_000) fun aPanelGoneBeforeItsFirstLayoutLeavesNothingRunning() {
        val rig = Rig()
        kotlinx.coroutines.runBlocking { rig.prime(12, docJson(12)) }
        writable(rig.player.isPlaying).value = true
        sectionTree(rig) { tree -> assertTrue(tree.texts().contains("from Lantern Song.lrc")) }
        writable(rig.player.target).value = PlayTarget.Room("kitchen")
        writable(rig.player.remote).value = RemoteNowPlaying(
            "kitchen", "play", "Lantern Song", "The Example Band", 13.0, 205.0, trackId = 12, readAtMs = 0,
        )
        sectionTree(rig, follow = LyricsFollow.Room("kitchen")) { tree -> assertTrue(tree.texts().contains("timing")) }
    }

    // ── the room cards ──────────────────────────────────────────────────

    private fun cardTexts(rig: Rig, vararg rooms: NowPlayingRoom, tick: Int = 0): List<String> {
        val tree = NodeTree()
        var texts = emptyList<String>()
        withHeadlessWindow {
            composeTest(tree) {
                setContent {
                    HeadlessUi {
                        CompositionLocalProvider(LocalApp provides rig.app) {
                            NowPlayingStrip(rooms.toList(), tick, onPlayRandom = {}, transport = RoomTransport { _, _ -> }, onFavorite = {})
                        }
                    }
                }
                // A playing room's "live" pill pulses for as long as it shows.
                settle(untilQuiet = false, frames = 20)
                texts = tree.texts()
            }
        }
        return texts
    }

    private fun kitchen(trackId: Long?, elapsed: Double, state: String = "play") = NowPlayingRoom(
        roomId = "kitchen", state = state, elapsedSec = elapsed, trackId = trackId,
        song = NowPlayingSong(title = "Lantern Song", artist = "The Example Band", durationSec = 205.0),
    )

    @Test fun aRoomCardShowsTheLineTheRoomIsSinging() {
        val rig = Rig()
        kotlinx.coroutines.runBlocking { rig.prime(12, docJson(12)) }
        // 12.0 s plus the card's 1 s tick: the first line.
        assertTrue(cardTexts(rig, kitchen(12, 12.0), tick = 1).contains("the lantern hums beside the river door"))
        // Paused: the tick does not count.
        assertTrue(cardTexts(rig, kitchen(12, 12.0, state = "pause"), tick = 5).contains("♪"))
        assertTrue(cardTexts(rig, kitchen(12, 17.0)).contains("and every copper kettle sings at dawn"))
        // The room's nudge (lyrics later) applies on the card too.
        rig.nudges.value = mapOf("kitchen" to 1_000L)
        assertFalse(cardTexts(rig, kitchen(12, 17.0)).contains("and every copper kettle sings at dawn"))
    }

    @Test fun aRoomCardShowsNoLineWithoutTimedLyrics() {
        val rig = Rig()
        kotlinx.coroutines.runBlocking {
            rig.prime(21, docJson(21, status = "plain", text = "the river door is open tonight"))
        }
        val plain = cardTexts(rig, kitchen(21, 12.0))
        assertFalse(plain.contains("the river door is open tonight"))
        assertFalse(plain.contains("♪"))
        // A stream: no track, no lyrics read at all.
        val requests = server.requestCount
        val stream = cardTexts(rig, kitchen(null, 12.0))
        assertTrue(stream.contains("Lantern Song"))
        assertFalse(stream.contains("♪"))
        assertEquals(requests, server.requestCount)
    }

    @Test fun nowPlayingCarriesTheRoomsLibraryTrack() {
        val rows = DomovoiJson.parseToJsonElement(
            """[{"room_id": "kitchen", "state": "play", "track_id": 12, "elapsed_sec": 3.0},
                {"room_id": "den", "state": "stop", "track_id": null}]""",
        ).decode<List<NowPlayingRoom>>()
        assertEquals(12L, rows[0].trackId)
        assertNull(rows[1].trackId)
    }
}
