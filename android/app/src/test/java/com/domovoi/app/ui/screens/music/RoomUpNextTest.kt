package com.domovoi.app.ui.screens.music

import com.domovoi.app.listens.ListenerPicks
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.testing.Bytecode
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
import org.junit.Test
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Up next P4 in the rooms (design-notes/MOBILE-HANDOFF-UPNEXT.md "Rooms"):
 * a room queue's picks with their reasons, the "up next" head, the room's
 * autoplay switch, "less like this", the real jump, play next / add with
 * `where`, save as playlist, and "play in <room>" starting the room's mix.
 * The JSON is the contract's (docs/API_REFERENCE.md §3.5a, §3.5f; the
 * core's queue_view in domovoi/up_next/rooms.py), not shaped to this code.
 */
class RoomUpNextTest {

    // ── the room queue's read, as the web hop answers it ─────────────────

    /** office playing song 70; 80 asked for; 90 and 91 picked by Up next (a list session). */
    private val withSession = """{"room_id":"office","items":[
        {"song_id":60,"pos":0,"file":"Kettle Band/Cellar/01 Lantern.mp3","title":"Lantern","artist":"Kettle Band","album":"Cellar","duration_sec":200,
         "added_by":"kitchen tablet","added_by_device_id":null,"added_at":"2026-11-12T10:00:00Z","guest_device_id":null,"guest_source":null,
         "playing":false,"origin":"auto","reason_label":"same artist","up_next_seq":1,"track_id":12},
        {"song_id":70,"pos":1,"file":"Kettle Band/Cellar/02 Moth.mp3","title":"Moth","artist":"Kettle Band","album":"Cellar","duration_sec":180,
         "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
         "playing":true,"origin":"explicit","reason_label":null,"up_next_seq":null,"track_id":13},
        {"song_id":80,"pos":2,"file":"http://radio.example/stream","title":null,"artist":null,"album":null,"duration_sec":null,
         "added_by":"Pat's phone","added_by_device_id":null,"added_at":"2026-11-12T10:02:00Z","guest_device_id":null,"guest_source":null,
         "playing":false,"origin":"explicit","reason_label":null,"up_next_seq":null,"track_id":null},
        {"song_id":90,"pos":3,"file":"Owls/Dusk/03 Field.mp3","title":"Field","artist":"Owls","album":"Dusk","duration_sec":240,
         "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
         "playing":false,"origin":"auto","reason_label":"from road trip","up_next_seq":4,"track_id":31},
        {"song_id":91,"pos":4,"file":"Owls/Dusk/04 Hollow.mp3","title":"Hollow","artist":"Owls","album":"Dusk","duration_sec":210,
         "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
         "playing":false,"origin":"auto","reason_label":"90s","up_next_seq":5,"track_id":32}],
      "current_song_id":70,"editable":true,"blocked_reason":null,"guest":false,"admin":false,
      "session_id":"7c1e2a90-1b7f-4f3a-9d0e-5a2b3c4d5e6f","autoplay":true,
      "up_next":{"session_id":"7c1e2a90-1b7f-4f3a-9d0e-5a2b3c4d5e6f","mode":"list","autoplay":true,
        "seed":{"track_id":13,"title":"Moth","artist":"Kettle Band","label":null,"list":{"kind":"playlist","name":"road trip"}},
        "ended":false}}"""

    /** The same room with no session: the Up next fields all null. */
    private val noSession = """{"room_id":"office","items":[
        {"song_id":70,"pos":0,"file":"b.mp3","title":"Moth","artist":"Kettle Band","album":null,"duration_sec":180,
         "added_by":null,"added_by_device_id":null,"added_at":null,"guest_device_id":null,"guest_source":null,
         "playing":true,"origin":"explicit","reason_label":null,"up_next_seq":null,"track_id":13}],
      "current_song_id":70,"editable":true,"blocked_reason":null,"guest":false,"admin":false,
      "session_id":null,"autoplay":null,"up_next":null}"""

    private fun queue(json: String) = DomovoiJson.parseToJsonElement(json).decode<RoomQueue>()

    @Test fun theQueueReadDecodesItsPicksAndTheRoomsSession() {
        val q = queue(withSession)
        assertEquals(listOf("auto", "explicit", "explicit", "auto", "auto"), q.items.map { it.origin })
        assertEquals("from road trip", q.items[3].reasonLabel)
        assertEquals(4, q.items[3].upNextSeq)
        assertEquals(31L, q.items[3].trackId)
        assertNull(q.items[2].trackId)
        assertEquals("7c1e2a90-1b7f-4f3a-9d0e-5a2b3c4d5e6f", q.sessionId)
        assertEquals(true, q.autoplay)
        assertFalse(q.guest)
        val u = q.upNext!!
        assertEquals("list", u.mode)
        assertEquals("Moth", u.seed?.title)
        assertEquals("road trip", u.seed?.list?.name)
        assertFalse(u.ended)
    }

    @Test fun withoutASessionNothingOfUpNextShows() {
        val q = queue(noSession)
        assertNull(q.sessionId)
        assertNull(q.upNext)
        assertEquals("", roomUpNextBasis(q))
        assertNull(roomUpNextLine(q, 0))
        assertEquals(listOf(RoomQueueLine.Entry(0)), roomQueueLines(q, q.items))
        val c = roomQueueControls(q)
        assertFalse(c.autoplay)
        assertFalse(c.lessLike)
    }

    // ── the head, the picks, the one line ───────────────────────────────

    @Test fun theHeadSaysWhatTheMixIsBuiltFrom() {
        val seed = RoomUpNextSeed(title = "Moth", list = RoomUpNextList("playlist", "road trip"))
        assertEquals("shuffled from road trip", upNextBasis("list", seed))
        assertEquals("more like road trip", upNextBasis("continue", seed))
        assertEquals("based on Moth", upNextBasis("library", seed.copy(list = null)))
        assertEquals("based on Moth", upNextBasis("list", seed.copy(list = null)))
        assertEquals("", upNextBasis("library", null))
    }

    @Test fun theHeadComesBeforeTheFirstPickAfterThePlayingSong() {
        val q = queue(withSession)
        // Song 60 is a pick, but before the playing one: not waiting.
        assertEquals(listOf(3, 4), roomUpNextRows(q.items))
        assertEquals(
            listOf(
                RoomQueueLine.Entry(0), RoomQueueLine.Entry(1), RoomQueueLine.Entry(2),
                RoomQueueLine.UpNextHead, RoomQueueLine.Entry(3), RoomQueueLine.Entry(4),
            ),
            roomQueueLines(q, q.items),
        )
        // No pick waiting: the head (and its line) after the list.
        val none = q.items.take(3)
        assertEquals(RoomQueueLine.UpNextHead, roomQueueLines(q, none).last())
    }

    @Test fun theOneMutedLine() {
        val q = queue(withSession)
        assertNull(roomUpNextLine(q, 2))
        assertEquals("similar songs join here when the queue is nearly over", roomUpNextLine(q, 0))
        val off = q.copy(autoplay = false)
        assertEquals("autoplay is off — stops after the last song of road trip", roomUpNextLine(off, 2))
        assertEquals("autoplay is off — the queue stops at the end", roomUpNextLine(off, 0))
        val library = off.copy(upNext = off.upNext!!.copy(mode = "library"))
        assertEquals("autoplay is off — the queue stops at the end", roomUpNextLine(library, 2))
    }

    @Test fun lessLikeThisIsOfferedOnlyOnAWaitingPick() {
        val q = queue(withSession)
        val c = roomQueueControls(q)
        assertEquals(listOf(false, false, false, true, true), q.items.indices.map { lessLikeOffered(c, q.items, it) })
    }

    @Test fun aGuestGetsTheJumpButNoSwitchNoLessLikeNoSaveNoClear() {
        val c = roomQueueControls(queue(withSession).copy(guest = true))
        assertTrue(c.playNow)
        assertFalse(c.autoplay)
        assertFalse(c.lessLike)
        assertFalse(c.save)
        assertFalse(c.clear)
        val household = roomQueueControls(queue(withSession))
        assertTrue(household.playNow && household.autoplay && household.lessLike && household.save && household.clear)
        // A blocked device watches: no jump, no switch.
        val blocked = roomQueueControls(queue(withSession).copy(editable = false, blockedReason = "blocked"))
        assertFalse(blocked.playNow)
        assertFalse(blocked.autoplay)
    }

    @Test fun picksTakenBackLeaveTheViewForTheirServerRoomAndSessionOnly() {
        val q = queue(withSession)
        val sid = q.sessionId!!
        val dropped = DroppedPicks("http://house-a:6370", "office", sid, setOf(4))
        assertEquals(listOf(60L, 70L, 80L, 91L), visibleRoomItems(q, dropped, "http://house-a:6370", "office").map { it.songId })
        // Another house, another room, another session: nothing hidden.
        assertEquals(5, visibleRoomItems(q, dropped, "http://house-b:6370", "office").size)
        assertEquals(5, visibleRoomItems(q, dropped, "http://house-a:6370", "kitchen").size)
        assertEquals(5, visibleRoomItems(q, dropped.copy(sessionId = "other"), "http://house-a:6370", "office").size)
        // Never the song playing, whatever the numbers say.
        val playing = q.copy(items = q.items.map { if (it.songId == 90L) it.copy(playing = true) else it.copy(playing = false) })
        assertTrue(visibleRoomItems(playing, dropped, "http://house-a:6370", "office").any { it.songId == 90L })
    }

    @Test fun aMoveLandsAtTheRowsServerPosition() {
        val q = queue(withSession)
        val shown = visibleRoomItems(q, DroppedPicks("s", "office", q.sessionId!!, setOf(4)), "s", "office")
        // Dragged onto the last visible row (Hollow, server position 4, visible index 3).
        assertEquals(4, roomMoveTarget(shown, 3))
    }

    // ── the words ────────────────────────────────────────────────────────

    @Test fun saveAsPlaylistSaysTheWebsSentence() {
        assertEquals(
            "Saved as \"road trip 2\" (3 songs); 2 left out: not songs from the library.",
            roomSaveNote(RoomSaveResult(9, "road trip 2", saved = 3, skipped = 2), "x"),
        )
        assertEquals("Saved as \"Mix\" (1 song).", roomSaveNote(RoomSaveResult(9, "Mix", saved = 1), "x"))
        assertEquals(
            "Saved as \"Mix\" (2 songs); 1 left out: not songs from the library, 1 repeat saved once.",
            roomSaveNote(RoomSaveResult(9, "Mix", saved = 2, skipped = 1, duplicates = 1), "x"),
        )
        assertEquals("no more Owls in this mix (2 more taken out)", lessLikeNote("Owls", 3))
        assertEquals("no more Owls in this mix", lessLikeNote("Owls", 1))
        assertEquals("autoplay off in office — the queue stops at the end", roomAutoplayNote("office", false))
    }

    @Test fun theWordsAreTheWebsOwn() {
        val web = listOf("../../web/static/components.jsx", "../web/static/components.jsx")
            .map(::File).first { it.isFile }.readText()
        for (line in listOf(
            "`shuffled from \${list}`", "`more like \${list}`", "`based on \${u.seed.title}`",
            "`autoplay is off — stops after the last song of \${name || 'this list'}`",
            "'autoplay is off — the queue stops at the end'",
            "'similar songs join here when the queue is nearly over'",
            "left out: not songs from the library`", "saved once`",
        )) assertTrue("not on the web: $line", line in web)
        val music = listOf("../../web/static/music.jsx", "../web/static/music.jsx")
            .map(::File).first { it.isFile }.readText()
        for (line in listOf(
            "`no more \${item.artist || 'of that'} in this mix\${n ? ` (\${n} more taken out)` : ''}`",
            "`autoplay off in \${room} — the queue stops at the end`",
            "`autoplay in \${room}: keep playing similar songs when the queue ends`",
        )) assertTrue("not on the web: $line", line in music)
    }

    // ── the routes ───────────────────────────────────────────────────────

    private val calls = CopyOnWriteArrayList<String>()
    private val answers = java.util.concurrent.ConcurrentHashMap<String, MockResponse>()
    private val server = MockWebServer().apply {
        dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val body = request.body.readUtf8()
                calls += "${request.method} $path" + if (body.isNotBlank() && body != "{}") " $body" else ""
                return answers[path] ?: MockResponse().setBody("""{"ok":true}""")
            }
        }
        start()
    }
    private val api = ApiClient({ server.url("/").toString().trimEnd('/') })
    private val rooms = RoomQueueApi(api) { "phone-7" }

    @After fun tearDown() = server.shutdown()

    @Test fun aRowTapIsTheRoomsOwnJumpAndNothingElse() = runBlocking {
        rooms.play("office", 90)
        assertEquals(listOf("""POST /api/music/queue/office/play {"song_id":90,"device_id":"phone-7"}"""), calls.toList())
    }

    @Test fun playNextAndAddToQueueSayWhere() = runBlocking {
        answers["/api/music/queue/office/add"] = MockResponse().setBody("""{"queued":[{"song_id":95,"file":"x"}],"requested":1,"started":false}""")
        assertEquals(1, rooms.playNext("office", listOf(12)))
        assertEquals(listOf(95L), rooms.add("office", listOf(13)))
        assertEquals(
            listOf(
                """POST /api/music/queue/office/add {"track_ids":[12],"device_id":"phone-7","where":"next"}""",
                """POST /api/music/queue/office/add {"track_ids":[13],"device_id":"phone-7","where":"end"}""",
            ),
            calls.toList(),
        )
    }

    @Test fun theAutoplaySwitchAndLessLikeThisGoToTheRoomsSession() = runBlocking {
        val sid = "7c1e2a90-1b7f-4f3a-9d0e-5a2b3c4d5e6f"
        answers["/api/music/up-next/sessions/$sid/feedback"] = MockResponse().setBody("""{"removed":[4,5]}""")
        rooms.setAutoplay(sid, false)
        assertEquals(listOf(4, 5), rooms.lessLikeThis(sid, 4))
        assertEquals(
            listOf(
                """PATCH /api/music/up-next/sessions/$sid {"autoplay":false}""",
                """POST /api/music/up-next/sessions/$sid/feedback {"seq":4,"kind":"less_like_this"}""",
            ),
            calls.toList(),
        )
    }

    @Test fun savingTheRoomsQueueSaysWhatTheServerSaid() = runBlocking {
        val path = "/api/music/queue/office/save-playlist"
        answers[path] = MockResponse().setResponseCode(201)
            .setBody("""{"playlist_id":41,"name":"road trip 2","saved":3,"skipped":2,"duplicates":0}""")
        val flow = SaveAsPlaylistFlow()
        assertFalse("saved without a name", flow.saveRoom { rooms.savePlaylist("office", it) })
        assertTrue(calls.isEmpty())
        flow.ask(); flow.name = "  road trip 2 "
        assertTrue(flow.saveRoom { rooms.savePlaylist("office", it) })
        assertEquals("""POST $path {"name":"road trip 2","device_id":"phone-7"}""", calls.single())
        assertEquals("Saved as \"road trip 2\" (3 songs); 2 left out: not songs from the library.", flow.note)
        assertFalse(flow.open)

        // 409: the name is taken — the server's sentence.
        answers[path] = MockResponse().setResponseCode(409)
            .setBody("""{"detail":"playlist named 'road trip' already exists (id=4)"}""")
        flow.ask(); flow.name = "road trip"
        assertFalse(flow.saveRoom { rooms.savePlaylist("office", it) })
        assertEquals("Couldn't save the playlist: playlist named 'road trip' already exists (id=4)", flow.note)
        assertTrue("the name field closed on a refusal", flow.open)

        // 422: nothing in the queue is a library song.
        answers[path] = MockResponse().setResponseCode(422)
            .setBody("""{"detail":"nothing in office's queue is a song from the library"}""")
        assertFalse(flow.saveRoom { rooms.savePlaylist("office", it) })
        assertEquals("Couldn't save the playlist: nothing in office's queue is a song from the library", flow.note)
    }

    // ── "play in <room>": the room's Up next starts ──────────────────────

    @Test fun playInARoomSendsTheListTheListenerAndWhoAsks() {
        val genre = UpNextContext.genre(GenreRow("hip hop", "Hip Hop", 40))
        assertEquals(
            """{"room_id":"office","track_id":12,"device_id":"phone-7","context":{"kind":"genre","ref":"hip hop","name":"Hip Hop"},"listener_person_id":3}""",
            roomPlayTrackBody("office", 12, "phone-7", genre, pick = "3", shared = false).toString(),
        )
        // "household" on purpose.
        assertEquals(
            """{"room_id":"office","track_id":12,"device_id":"phone-7","as_household":true}""",
            roomPlayTrackBody("office", 12, "phone-7", null, pick = ListenerPicks.HOUSEHOLD, shared = false).toString(),
        )
        // Nothing picked, or a shared screen: the server decides; never `up_next:false`.
        val plain = """{"room_id":"office","track_id":12,"device_id":"phone-7"}"""
        assertEquals(plain, roomPlayTrackBody("office", 12, "phone-7", null, pick = null, shared = false).toString())
        assertEquals(plain, roomPlayTrackBody("office", 12, "phone-7", null, pick = "3", shared = true).toString())
        assertEquals(
            """{"room_id":"office","track_id":12,"device_id":"phone-7","autoplay":false}""",
            roomPlayTrackBody("office", 12, "phone-7", null, null, false, autoplay = false).toString(),
        )
        // The "no genre" row has no key a mix can name (the server's 422).
        assertNull(UpNextContext.genre(GenreRow("", "no genre", 3)))
    }

    @Test fun theTrackDrawerPlaysInARoomThroughTheUpNextBody() {
        val calls = Bytecode.callers(
            fileFacade = "com.domovoi.app.ui.screens.music.TrackDrawerKt",
            owner = "com/domovoi/app/ui/screens/music/RoomUpNextKt",
            // `$default`: the call that leaves autoplay at its default.
            names = setOf("roomPlayTrackBody", "roomPlayTrackBody\$default"),
        )
        assertTrue("the drawer's play in room skips the Up next body", calls.isNotEmpty())
    }

    // ── a playlist in a room: the whole playlist is the room's queue ────

    @Test fun aPlaylistInARoomSendsTheListenerLikePlayInRoom() {
        assertEquals(
            """{"room_id":"office","playlist_id":7,"shuffle":true,"device_id":"phone-7","listener_person_id":3}""",
            roomPlayPlaylistBody("office", 7, shuffle = true, deviceId = "phone-7", pick = "3", shared = false).toString(),
        )
        assertEquals(
            """{"room_id":"office","playlist_id":7,"shuffle":false,"device_id":"phone-7","as_household":true}""",
            roomPlayPlaylistBody("office", 7, false, "phone-7", ListenerPicks.HOUSEHOLD, shared = false).toString(),
        )
        // A shared screen names nobody; autoplay is never sent (the server's default) until P8.
        assertEquals(
            """{"room_id":"office","playlist_id":7,"shuffle":false,"device_id":"phone-7"}""",
            roomPlayPlaylistBody("office", 7, false, "phone-7", "3", shared = true).toString(),
        )
    }

    @Test fun bothPlaylistPlayButtonsGoThroughThatBody() {
        for (facade in listOf("MusicScreenKt", "PlaylistDrawerKt")) {
            val calls = Bytecode.callers(
                fileFacade = "com.domovoi.app.ui.screens.music.$facade",
                owner = "com/domovoi/app/ui/screens/music/RoomUpNextKt",
                names = setOf("playPlaylistInRoom"),
            )
            assertTrue("$facade plays a playlist in a room without the listener", calls.isNotEmpty())
        }
    }

    @Test fun thePlaylistAnswerIsReadNewOrOld() {
        // The contract's answer (API_REFERENCE §3.5 / the core's play-playlist).
        val now = playPlaylistAnswer(DomovoiJson.parseToJsonElement(
            """{"played":true,"playlist_id":7,"playlist_name":"road trip","mode":"playlist","track_id":12,
               "title":"Lantern","artist":"Kettle Band","latency_ms":41,"total":180,"queued":50,"skipped":2,
               "loading":true,"up_next":{"session_id":"7c1e2a90-1b7f-4f3a-9d0e-5a2b3c4d5e6f"}}""",
        ))!!
        assertEquals(180, now.total)
        assertEquals(50, now.queued)
        assertEquals(2, now.skipped)
        assertTrue(now.loading)
        assertEquals("7c1e2a90-1b7f-4f3a-9d0e-5a2b3c4d5e6f", now.upNext?.sessionId)
        // An older server: none of it, nothing loading.
        val old = playPlaylistAnswer(DomovoiJson.parseToJsonElement(
            """{"played":true,"playlist_id":7,"playlist_name":"road trip","mode":"playlist","track_id":12,"title":"Lantern","artist":"Kettle Band","latency_ms":41}""",
        ))!!
        assertFalse(old.loading)
        assertNull(old.total)
        assertNull(old.upNext)
        assertFalse(playPlaylistAnswer(DomovoiJson.parseToJsonElement("""{"loading":null,"up_next":null}"""))!!.loading)
        assertNull(playPlaylistAnswer(kotlinx.serialization.json.JsonNull))
    }

    @Test fun aLoadingPlaylistIsReadAgainAFewTimesThenNever() = runBlocking {
        val slept = mutableListOf<Long>()
        val asked = mutableListOf<Pair<String, String>>()
        val loading = PlayPlaylistAnswer(total = 180, queued = 50, loading = true)
        val n = followPlaylistLoading(loading, "a", "office", { "a" }, { slept += it }, { s, r -> asked += s to r })
        assertEquals(3, n)
        assertEquals(listOf(2_000L, 4_000L, 9_000L), slept)
        assertEquals(List(3) { "a" to "office" }, asked)

        // Not loading (or an older server): never.
        asked.clear()
        assertEquals(0, followPlaylistLoading(loading.copy(loading = false), "a", "office", { "a" }, { }, { s, r -> asked += s to r }))
        assertEquals(0, followPlaylistLoading(null, "a", "office", { "a" }, { }, { s, r -> asked += s to r }))
        assertTrue(asked.isEmpty())

        // The app moved to another server: that house's queue is not asked.
        var serverNow = "a"
        val moved = followPlaylistLoading(loading, "a", "office", { serverNow }, { serverNow = "b" }, { s, r -> asked += s to r })
        assertEquals(0, moved)
        assertTrue(asked.isEmpty())
    }
}
