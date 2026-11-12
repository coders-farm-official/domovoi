package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.player.PlayTarget
import com.domovoi.app.testing.Bytecode
import com.domovoi.app.testing.CastRig
import com.domovoi.app.testing.libraryQueue
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.random.Random

/**
 * Batch 2 §2 (owner priority): the queue's search box, Play next / Add to
 * queue for the phone and for a room, a room's queue routes, and "save as
 * playlist". The web's player.jsx (wf/queue-add-search, merge 9a7e349c) is
 * the reference: its prompts, paths and words are read from the file itself.
 */
class QueueAddSearchTest {

    // ── the web's own source ─────────────────────────────────────────────

    private val web: String = listOf("../../web/static/player.jsx", "../web/static/player.jsx")
        .map(::File).first { it.isFile }.readText()

    /** The string literals of `const <name> = [ … ];` in player.jsx. */
    private fun webList(name: String): List<String> {
        val body = Regex("""const $name = \[(.*?)\];""", RegexOption.DOT_MATCHES_ALL).find(web)?.groupValues?.get(1)
            ?: error("$name is not in player.jsx")
        return Regex(""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'""").findAll(body)
            .map { m -> m.groupValues[1].ifEmpty { m.groupValues[2] } }.toList()
    }

    private fun webConst(name: String): Int =
        Regex("""const $name = (\d+);""").find(web)?.groupValues?.get(1)?.toInt() ?: error("$name is not in player.jsx")

    @Test fun thePromptsAreTheWebsInItsOrder() {
        val webPrompts = webList("_QUEUE_ASK_PROMPTS")
        assertEquals(15, webPrompts.size)
        assertEquals(webPrompts, QUEUE_ASK_PROMPTS)
        assertEquals("What's next? Name a song, artist or vibe…", QUEUE_ASK_PROMPTS.first())
    }

    @Test fun thePromptRotates() {
        // One picked at random each time a queue opens: every prompt comes up.
        val seen = (0 until 2_000).map { queueAskPrompt(Random(it)) }.toSet()
        assertEquals(QUEUE_ASK_PROMPTS.toSet(), seen)
        // And the same seed is the same pick (a box keeps its prompt while typing).
        assertEquals(queueAskPrompt(Random(7)), queueAskPrompt(Random(7)))
    }

    @Test fun theSearchLimitsAreTheWebs() {
        assertEquals(webConst("_QUEUE_SEARCH_MIN"), QUEUE_SEARCH_MIN)
        assertEquals(webConst("_QUEUE_GENRE_TAKE"), QUEUE_GENRE_TAKE)
        assertTrue("useDebouncedValue(q.trim(), $QUEUE_SEARCH_DEBOUNCE_MS)" in web)
    }

    @Test fun theSearchAsksWhatTheWebAsks() {
        // The web's two reads, their fields (q = title, artist or album; genres) and paging.
        assertTrue("/api/music/library?q=\${enc}&limit=8" in web)
        assertTrue("/api/music/genres?q=\${enc}&limit=2" in web)
        assertTrue("/api/music/library?genre=\${encodeURIComponent(row.genre.key)}&limit=200" in web)

        val p = queueSearchPaths("AC/DC & 'n' (live)~!")
        // encodeURIComponent's encoding, exactly.
        assertEquals("/api/music/library?q=AC%2FDC%20%26%20'n'%20(live)~!&limit=8", p.songs)
        assertEquals("/api/music/genres?q=AC%2FDC%20%26%20'n'%20(live)~!&limit=2", p.genres)
        assertEquals("/api/music/library?genre=hip%20hop&limit=200", queueGenreSongsPath("hip hop"))

        // Two characters ask; one doesn't; the ends are trimmed (as the web's dq).
        assertNull(queueSearchTerm(" a "))
        assertEquals("ab", queueSearchTerm("  ab "))
    }

    @Test fun theBoxSaysWhatTheWebSays() {
        // Each of the web's lines, with its template holes, is ours with the same holes.
        for (line in listOf(
            queueNothingMatches("{dq}"),
            queueSearchFailed("\${apiErrorText(e)}"),
            queueNothingToAdd("\${label(row)}"),
            queueAddFailed("\${label(row)}", "\${apiErrorText(e)}"),
            playNextLabel("\${label(row)}"),
            addToQueueLabel("\${label(row)}"),
            QUEUE_SEARCH_LABEL, QUEUE_SEARCHING,
            SAVE_AS_PLAYLIST, SAVE_AS_PLAYLIST_HINT, SAVE_AS_PLAYLIST_NONE, PLAYLIST_NAME_PROMPT,
            savedAsNote("\${name.trim()}"),
            saveFailedNote("\${apiErrorText(e)}"),
        )) assertTrue("not on the web: $line", line in web)

        assertEquals("Added: Lantern", queueAddedNote("Lantern", 1, next = false))
        assertEquals("Playing next: Jazz songs (25 songs)", queueAddedNote("Jazz songs", 25, next = true))
        assertEquals("Jazz songs", queueFindLabel(QueueFind.Genre(GenreRow("jazz", "Jazz", 1))))
        assertEquals("that song", queueFindLabel(QueueFind.Song(LibraryTrack(id = 1))))
        assertEquals("genre · 1 song, shuffled", queueFindSub(QueueFind.Genre(GenreRow("jazz", "Jazz", 1))))
        assertEquals("genre · 9 songs, shuffled", queueFindSub(QueueFind.Genre(GenreRow("jazz", "Jazz", 9))))
    }

    // ── a fake web server, recording what it is asked ───────────────────

    private val calls = CopyOnWriteArrayList<String>()
    private val answers = java.util.concurrent.ConcurrentHashMap<String, MockResponse>()
    private var onRequest: (RecordedRequest) -> Unit = {}
    private val server = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val body = request.body.readUtf8()
                calls += "${request.method} $path" + if (body.isNotBlank() && body != "{}") " $body" else ""
                onRequest(request)
                answers[path]?.let { return it }
                return MockResponse().setBody("""{"ok":true}""")
            }
        }
        start()
    }
    @Volatile private var serverKey = "house-a"
    private val api = ApiClient({ server.url("/").toString().trimEnd('/') })
    private val rooms = RoomQueueApi(api, { serverKey }, { "phone-7" })
    private var rig: CastRig? = null

    @After fun tearDown() {
        server.shutdown()
        rig?.close()
    }

    private fun json(s: String) = MockResponse().setBody(s)
    private fun posts() = calls.filter { it.startsWith("POST") }

    private val lantern = LibraryTrack(id = 12, title = "Lantern", artist = "Kettle Band", album = "Cellar")
    private val moth = LibraryTrack(id = 13, title = "Moth", artist = "Kettle Band", album = "Cellar")

    /** office's queue: song 70 playing at position 1 of 3. */
    private fun officeQueue() {
        answers["/api/music/queue/office?device_id=phone-7"] = json(
            """{"room_id":"office","current_song_id":70,"editable":true,"items":[
               {"song_id":60,"pos":0,"file":"a.mp3"},
               {"song_id":70,"pos":1,"file":"b.mp3","playing":true},
               {"song_id":80,"pos":2,"file":"c.mp3"}]}""",
        )
    }

    @Test fun theSearchReadsSongsAndGenresAndKeepsKeyedGenres() = runBlocking {
        answers["/api/music/library?q=jaz&limit=8"] = json("""{"total":1,"items":[{"id":5,"title":"So What","artist":"Miles","album":"Kind of Blue"}]}""")
        answers["/api/music/genres?q=jaz&limit=2"] = json(
            """{"items":[{"key":"jazz","name":"Jazz","track_count":40},{"key":"","name":"no genre","track_count":3}],"reading":false}""",
        )
        val finds = queueSearch(api, "jaz")
        assertEquals(listOf("s5", "gjazz"), finds.map { it.key })
        assertEquals(setOf("GET /api/music/library?q=jaz&limit=8", "GET /api/music/genres?q=jaz&limit=2"), calls.toSet())
    }

    @Test fun aGenreReadThatFailsOnlyLosesTheGenres() = runBlocking {
        answers["/api/music/library?q=jaz&limit=8"] = json("""{"items":[{"id":5,"title":"So What"}]}""")
        answers["/api/music/genres?q=jaz&limit=2"] = MockResponse().setResponseCode(500)
        assertEquals(listOf("s5"), queueSearch(api, "jaz").map { it.key })
    }

    // ── Play next / Add to queue: the phone, or the room ────────────────

    private fun adder(rig: CastRig, shuffler: (List<LibraryTrack>) -> List<LibraryTrack> = { it }) =
        QueueAdder(rig.player.asPhoneQueue(), rooms, { key -> api.get(queueGenreSongsPath(key)).decode<LibraryPage>().items }, shuffler)

    @Test fun thePhonesQueueTakesItHereAndNoRoomIsAsked() = runBlocking {
        val r = CastRig().also { rig = it }
        r.player.playItems(libraryQueue("one", "two", "three"))
        val a = adder(r)

        a.add(QueueTarget.Phone, listOf(lantern), next = false)
        assertEquals(listOf(101L, 102L, 103L, 12L), r.player.queue.value.map { it.id })

        a.add(QueueTarget.Phone, listOf(moth), next = true)
        assertEquals(listOf(101L, 13L, 102L, 103L, 12L), r.player.queue.value.map { it.id })
        assertTrue("a room was asked: $calls", calls.isEmpty())
    }

    @Test fun aRoomsQueueTakesItThroughItsAddRoute() = runBlocking {
        val r = CastRig().also { rig = it }
        answers["/api/music/queue/office/add"] = json("""{"queued":[{"song_id":91,"file":"x"}],"requested":1,"started":false}""")

        val took = adder(r).add(QueueTarget.Room("office"), listOf(lantern), next = false)

        assertEquals(1, took)
        assertEquals(listOf("""POST /api/music/queue/office/add {"track_ids":[12],"device_id":"phone-7"}"""), posts())
        // An empty phone queue stays empty: filling it would start the phone playing.
        assertTrue(r.player.queue.value.isEmpty())
    }

    @Test fun whileCastingThePhonesOwnQueueForTheRoomGetsItToo() = runBlocking {
        val r = CastRig().also { rig = it }
        r.player.playItems(libraryQueue("one", "two"))
        answers["/api/music/queue/office/add"] = json("""{"queued":[{"song_id":91}]}""")
        adder(r).add(QueueTarget.Room("office"), listOf(lantern), next = false)
        assertEquals(listOf(101L, 102L, 12L), r.player.queue.value.map { it.id })
    }

    @Test fun playNextInARoomAddsThenMovesEachRightAfterThePlayingSong() = runBlocking {
        val r = CastRig().also { rig = it }
        officeQueue()
        answers["/api/music/queue/office/add"] = json("""{"queued":[{"song_id":91},{"song_id":92}]}""")

        adder(r).add(QueueTarget.Room("office"), listOf(lantern, moth), next = true)

        assertEquals(
            listOf(
                """POST /api/music/queue/office/add {"track_ids":[12,13],"device_id":"phone-7"}""",
                """POST /api/music/queue/office/move {"song_id":91,"to_position":2,"device_id":"phone-7"}""",
                """POST /api/music/queue/office/move {"song_id":92,"to_position":3,"device_id":"phone-7"}""",
            ),
            posts(),
        )
    }

    @Test fun aSongRowFollowsThePlayer() {
        assertEquals(QueueTarget.Phone, queueTargetOf(PlayTarget.Local))
        assertEquals(QueueTarget.Room("office"), queueTargetOf(PlayTarget.Room("office")))
        assertEquals("playing next: \"Lantern\"", songQueuedToast(QueueTarget.Phone, "Lantern", true))
        assertEquals("queued \"Lantern\"", songQueuedToast(QueueTarget.Phone, "Lantern", false))
        assertEquals("playing next in office: \"Lantern\"", songQueuedToast(QueueTarget.Room("office"), "Lantern", true))
        assertEquals("added \"Lantern\" to office's queue", songQueuedToast(QueueTarget.Room("office"), "Lantern", false))
    }

    @Test fun everySongListQueuesThroughTheOneRouter() {
        // Library (MusicScreen's row lambdas), genre songs and playlist rows
        // all go through rememberSongQueue, which follows the player's target.
        for (facade in listOf("MusicScreenKt", "GenresTabKt", "PlaylistDrawerKt")) {
            val calls = Bytecode.callers(
                fileFacade = "com.domovoi.app.ui.screens.music.$facade",
                owner = "com/domovoi/app/ui/screens/music/QueueAddBoxKt",
                names = setOf("rememberSongQueue"),
            )
            assertTrue("$facade's song rows don't route Play next / Add to queue", calls.isNotEmpty())
        }
        // And the track drawer names the room or the phone itself.
        val drawer = Bytecode.callers(
            fileFacade = "com.domovoi.app.ui.screens.music.TrackDrawerKt",
            owner = "com/domovoi/app/ui/screens/music/QueueAddBoxKt",
            names = setOf("rememberSongQueueTo"),
        )
        assertTrue(drawer.isNotEmpty())
    }

    @Test fun aGenreFromTheBoxAddsAShuffledTwentyFive() = runBlocking {
        val r = CastRig().also { rig = it }
        val many = (1..60).joinToString(",") { """{"id":$it,"title":"t$it"}""" }
        answers["/api/music/library?genre=jazz&limit=200"] = json("""{"items":[$many]}""")
        val status = adder(r, shuffler = { it.reversed() })
            .addFind(QueueTarget.Phone, QueueFind.Genre(GenreRow("jazz", "Jazz", 60)), next = false)
        assertEquals("Added: Jazz songs (25 songs)", status)
        assertEquals((60L downTo 36L).toList(), r.player.queue.value.map { it.id })
    }

    @Test fun aRoomThatRefusesIsSaidInTheWebsWords() = runBlocking {
        val r = CastRig().also { rig = it }
        answers["/api/music/queue/office/add"] = MockResponse().setResponseCode(403)
            .setBody("""{"detail":"office's queue is blocked for this device"}""")
        val status = adder(r).addFind(QueueTarget.Room("office"), QueueFind.Song(lantern), next = false)
        assertEquals("Couldn't add Lantern: office's queue is blocked for this device", status)
    }

    // ── a room's queue routes ────────────────────────────────────────────

    @Test fun theRoomQueueRoutes() = runBlocking {
        officeQueue()
        val q = rooms.read("office")
        assertEquals(1, RoomQueueApi.playingPos(q))
        rooms.move("office", 80, 0)
        rooms.remove("office", listOf(60))
        rooms.clear("office")
        assertEquals(
            listOf(
                "GET /api/music/queue/office?device_id=phone-7",
                """POST /api/music/queue/office/move {"song_id":80,"to_position":0,"device_id":"phone-7"}""",
                """POST /api/music/queue/office/remove {"song_ids":[60],"device_id":"phone-7"}""",
                """POST /api/music/queue/office/clear {"device_id":"phone-7"}""",
            ),
            calls.toList(),
        )
    }

    @Test fun aRoomNameIsEncodedInTheRoute() = runBlocking {
        rooms.clear("living room")
        assertEquals("""POST /api/music/queue/living%20room/clear {"device_id":"phone-7"}""", calls.single())
    }

    @Test fun playNowMovesTheSongUpThenSkipsToIt() = runBlocking {
        officeQueue()
        val q = rooms.read("office")
        rooms.playNow("office", q, 60)   // before the playing song: lands at its place
        rooms.playNow("office", q, 80)   // already next: just the skip
        assertEquals(
            listOf(
                """POST /api/music/queue/office/move {"song_id":60,"to_position":1,"device_id":"phone-7"}""",
                "POST /api/music/skip/office",
                "POST /api/music/skip/office",
            ),
            posts(),
        )
    }

    @Test fun aServerChangeInsideTheRoundTripStopsTheChain() = runBlocking {
        officeQueue()
        answers["/api/music/queue/office/add"] = json("""{"queued":[{"song_id":91}]}""")
        // The app moves to another Domovoi while the add is on its way.
        onRequest = { if (it.path == "/api/music/queue/office/add") serverKey = "house-b" }
        try {
            rooms.playNext("office", listOf(12))
            fail("the chain went on against another server")
        } catch (_: ServerMovedException) {
        }
        assertFalse("a move went to the new server: $calls", calls.any { "/move" in it })
    }

    @Test fun anAddsAnswerNamesItsNewEntries() {
        val answer = DomovoiJson.parseToJsonElement("""{"queued":[{"song_id":5,"file":"a"},{"file":"b"},{"song_id":6}],"started":true}""")
        assertEquals(listOf(5L, 6L), RoomQueueApi.queuedSongIds(answer as kotlinx.serialization.json.JsonObject))
    }

    // ── added_by_device_id: null for a non-admin (main 67a588c7) ────────

    @Test fun aQueueWithoutDeviceIdsDecodesAndShowsOnlyTheName() {
        val q = DomovoiJson.parseToJsonElement(
            """{"room_id":"office","current_song_id":null,"editable":true,"items":[
               {"song_id":1,"pos":0,"file":"a.mp3","title":"A","artist":"X","duration_sec":200,
                "added_by":"kitchen tablet","added_by_device_id":null,"added_at":"2026-11-12T10:00:00Z","playing":false},
               {"song_id":2,"pos":1,"file":"b.mp3","title":"B","artist":"Y","duration_sec":null,
                "added_by":null,"added_by_device_id":null,"added_at":null,"playing":false}]}""",
        ).decode<RoomQueue>()
        assertNull(q.items[0].addedByDeviceId)
        assertEquals("X · 3:20 · added by kitchen tablet", roomQueueSubline(q.items[0]))
        assertEquals("Y", roomQueueSubline(q.items[1]))
        assertNull(RoomQueueApi.playingPos(q))
    }

    // ── save as playlist ─────────────────────────────────────────────────

    @Test fun saveAsPlaylistAsksForANameBeforeAnythingIsSent() = runBlocking {
        val flow = SaveAsPlaylistFlow()
        val ids = listOf(12L, 13L)
        assertFalse("saved without being asked for a name", flow.save(api, ids))
        flow.ask()
        assertTrue(flow.open)
        flow.name = "   "
        assertFalse(flow.save(api, ids))
        assertTrue("sent before a name: $calls", calls.isEmpty())

        answers["/api/playlists"] = json("""{"id":44,"name":"Road trip"}""")
        answers["/api/playlists/44/tracks"] = json("""{"ok":true}""")
        flow.name = "  Road trip "
        assertTrue(flow.save(api, ids))
        assertEquals(
            listOf(
                """POST /api/playlists {"name":"Road trip"}""",
                """POST /api/playlists/44/tracks {"track_id":12}""",
                """POST /api/playlists/44/tracks {"track_id":13}""",
            ),
            calls.toList(),
        )
        assertEquals("Saved as \"Road trip\".", flow.note)
        assertFalse(flow.open)
    }

    @Test fun aRefusedPlaylistIsSaid() = runBlocking {
        answers["/api/playlists"] = MockResponse().setResponseCode(409).setBody("""{"detail":"a playlist by that name exists"}""")
        val flow = SaveAsPlaylistFlow().apply { ask(); name = "Road trip" }
        assertFalse(flow.save(api, listOf(12L)))
        assertEquals("Couldn't save the playlist: a playlist by that name exists", flow.note)
        assertTrue(flow.open)
    }

    @Test fun onlyLibrarySongsAreSaved() {
        val q = libraryQueue("one", "two") + com.domovoi.app.player.PlayItem.fromStation(3, "Harbor FM")
        assertEquals(listOf(101L, 102L), savableTrackIds(q))
    }
}
