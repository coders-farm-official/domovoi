package com.domovoi.app.ui.screens.music

import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.testing.Bytecode
import kotlinx.serialization.json.JsonElement
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The room queue's three 2026-11-12 pieces (the web's Room queue tab, merge
 * 715a9681): adding from it only where adding works, re-reading it only when
 * THIS room's now-playing changed, and drawing a long queue 100 rows at a time.
 */
class RoomQueueWindowTest {

    private fun repoFile(rel: String): String =
        listOf("../../$rel", "../$rel").map(::File).first { it.isFile }.readText()

    // ── 1. which answer is this room's; who may add ─────────────────────

    /** The server's own RoomQueue model (web/backend/api/music_queue.py), not a fixture. */
    @Test fun theQueueFieldsAreTheServersOwn() {
        val src = repoFile("web/backend/api/music_queue.py")
        val model = src.substringAfter("class RoomQueue(BaseModel):").substringBefore("\nclass ")
        assertTrue(model, "room_id: str" in model)
        assertTrue(model, "editable: bool = True" in model)
        assertTrue(model, "blocked_reason: str | None = None" in model)
        // And they decode as the server sends them (a blocked device).
        val q = DomovoiJson.decodeFromString(
            RoomQueue.serializer(),
            """{"room_id":"office","items":[],"current_song_id":null,"editable":false,
               "blocked_reason":"office's queue is blocked for this device","guest":false}""",
        )
        assertEquals("office", q.roomId)
        assertFalse(q.editable)
        assertEquals("office's queue is blocked for this device", q.blockedReason)
        assertFalse(roomCanAdd(answered = true, queue = q))
        assertTrue(roomCanAdd(answered = true, queue = q.copy(editable = true)))
        assertFalse(roomCanAdd(answered = false, queue = q.copy(editable = true)))
    }

    @Test fun anotherRoomsOrServersAnswerIsNotThisRoomsQueue() {
        val office = RoomQueue(roomId = "office", items = listOf(QueueItem(songId = 1)))
        val read = RoomQueueRead("http://a", "office", office)
        assertSame(office, roomQueueFor(read, "http://a", "office"))
        // The kitchen was just picked: office's answer is still held, not shown.
        assertNull(roomQueueFor(read, "http://a", "kitchen"))
        // Another house with an "office" too.
        assertNull(roomQueueFor(read, "http://b", "office"))
        // The server says the answer is another room's.
        assertNull(roomQueueFor(RoomQueueRead("http://a", "office", office.copy(roomId = "den")), "http://a", "office"))
        assertNull(roomQueueFor(null, "http://a", "office"))
        assertNull(roomQueueFor(read, "http://a", null))
    }

    // ── 2. re-reading on now-playing ─────────────────────────────────────

    /** `music.now_playing.changed`'s data, shaped as realtime.py `_snapshot_now_playing` sends it. */
    private fun event(office: String, officeSong: String?, den: String, denSong: String?): JsonElement {
        fun room(state: String, song: String?) =
            """{"state":"$state","song":${song?.let { """{"file":"$it.mp3","title":"$it","artist":"x","album":null}""" } ?: "null"},"stream_url":null}"""
        return DomovoiJson.parseToJsonElement("""{"office":${room(office, officeSong)},"den":${room(den, denSong)}}""")
    }

    @Test fun anotherRoomsSongDoesNotReReadThisRoomsQueue() {
        val w = RoomPlayWatch()
        // The first event: what office was doing before is unknown here.
        assertTrue(w.shouldReread("s", "office", event("play", "Bravo", "play", "Echo")))
        // The den moves on: office's queue is not read again.
        assertFalse(w.shouldReread("s", "office", event("play", "Bravo", "play", "Foxtrot")))
        assertFalse(w.shouldReread("s", "office", event("play", "Bravo", "pause", "Foxtrot")))
        // Office's own song or state changes: read again, once.
        assertTrue(w.shouldReread("s", "office", event("play", "Charlie", "pause", "Foxtrot")))
        assertFalse(w.shouldReread("s", "office", event("play", "Charlie", "pause", "Foxtrot")))
        assertTrue(w.shouldReread("s", "office", event("pause", "Charlie", "pause", "Foxtrot")))
        assertTrue(w.shouldReread("s", "office", event("stop", null, "pause", "Foxtrot")))
    }

    @Test fun anEventWithoutTheRoomsStillReReads() {
        val w = RoomPlayWatch()
        assertTrue(w.shouldReread("s", "office", null))
        assertTrue(w.shouldReread("s", "office", DomovoiJson.parseToJsonElement("[]")))
        // No room on show: nothing to read.
        assertFalse(w.shouldReread("s", null, event("play", "Bravo", "play", "Echo")))
    }

    @Test fun aRoomPickedOrAServerChangedStartsOver() {
        val w = RoomPlayWatch()
        val ev = event("play", "Bravo", "play", "Echo")
        assertTrue(w.shouldReread("s", "office", ev))
        assertFalse(w.shouldReread("s", "office", ev))
        // The den now: its first event reads it.
        assertTrue(w.shouldReread("s", "den", ev))
        // Another house with the same rooms.
        assertTrue(w.shouldReread("t", "den", ev))
        // Picking a room on the screen forgets what was seen.
        val sel = RoomQueueSelection("office")
        assertTrue(sel.watch.shouldReread("s", "office", ev))
        sel.room = "den"
        sel.room = "office"
        assertTrue(sel.watch.shouldReread("s", "office", ev))
    }

    @Test fun theRoomQueueReReadsThroughTheWatchNotOnEveryEvent() {
        // rememberRoomQueue can't be composed on the JVM: its compiled code
        // must ask the watch, and must not hand the event to rememberApi
        // (which re-reads on every event, any room's).
        val asks = Bytecode.callers(
            fileFacade = "com.domovoi.app.ui.screens.music.QueueTabKt",
            owner = "com/domovoi/app/ui/screens/music/RoomPlayWatch",
            names = setOf("shouldReread"),
        )
        assertTrue("the room queue doesn't ask RoomPlayWatch", asks.isNotEmpty())
        val src = repoFile("android/app/src/main/java/com/domovoi/app/ui/screens/music/QueueTab.kt")
        val fn = src.substringAfter("fun rememberRoomQueue(").substringBefore("\n}\n")
        assertFalse("rememberApi re-reads on every now-playing event", Regex("""rememberApi\([^)]*eventTypes""").containsMatchIn(fn))
    }

    @Test fun theRoomSignatureIsTheWebs() {
        val web = repoFile("web/static/music.jsx")
        assertTrue(web.contains("const roomPlaySig = (np) => (np ? `\${np.state || ''}|\${np.song ? `\${np.song.file || ''}|\${np.song.title || ''}` : ''}` : '');"))
        val np = DomovoiJson.parseToJsonElement("""{"state":"play","song":{"file":"a.mp3","title":"A"}}""")
        assertEquals("play|a.mp3|A", roomPlaySig(np))
        assertEquals("stop|", roomPlaySig(DomovoiJson.parseToJsonElement("""{"state":"stop","song":null}""")))
        assertEquals("", roomPlaySig(null))
    }

    // ── 3. a long queue, 100 rows at a time ──────────────────────────────

    private fun queueOf(n: Int, playing: Int = -1, picksFrom: Int = -1) = (0 until n).map { i ->
        QueueItem(
            songId = 1_000L + i, pos = i, title = "song $i", playing = i == playing,
            origin = if (picksFrom in 0..i) "auto" else "explicit",
            upNextSeq = if (picksFrom in 0..i) i else null,
        )
    }

    private fun entries(lines: List<RoomQueueLine>) = lines.filterIsInstance<RoomQueueLine.Entry>().map { it.index }

    @Test fun theStepIsTheWebs() {
        val web = repoFile("web/static/music.jsx")
        assertTrue("const QUEUE_ROWS_STEP = $ROOM_QUEUE_ROWS_STEP;" in web)
        assertTrue("const QUEUE_FOLD_AFTER = $ROOM_QUEUE_FOLD_AFTER;" in web)
        assertTrue("show more ({items.length - to} more)" in web)
        assertEquals("show more (1400 more)", showMoreLabel(1_400))
        assertEquals("show 1 played song", showEarlierLabel(1))
        assertEquals("show 698 played songs", showEarlierLabel(698))
    }

    @Test fun aWholePlaylistIsDrawnAHundredAtATime() {
        val items = queueOf(1_500, playing = 0)
        val q = RoomQueue(roomId = "office", items = items)
        val first = roomQueueWindow(q, items, ROOM_QUEUE_ROWS_STEP, earlier = false)
        assertEquals((0 until 100).toList(), entries(first))
        assertEquals(RoomQueueLine.ShowMore(1_400), first.last())
        val more = roomQueueWindow(q, items, 2 * ROOM_QUEUE_ROWS_STEP, earlier = false)
        assertEquals((0 until 200).toList(), entries(more))
        // The end: no "show more".
        val all = roomQueueWindow(q, items, 1_500, earlier = false)
        assertEquals(1_500, entries(all).size)
        assertTrue(all.none { it is RoomQueueLine.ShowMore })
    }

    @Test fun deepIntoAQueueThePlayedSongsFoldAway() {
        val items = queueOf(1_500, playing = 700)
        val q = RoomQueue(roomId = "office", items = items)
        val lines = roomQueueWindow(q, items, ROOM_QUEUE_ROWS_STEP, earlier = false)
        assertEquals(RoomQueueLine.ShowEarlier(698), lines.first())
        // Entries keep their place in the WHOLE queue: up/down, a jump and
        // remove act on the right song past the first page.
        assertEquals((698 until 798).toList(), entries(lines))
        assertEquals(RoomQueueLine.ShowMore(702), lines.last())
        assertEquals(799, roomMoveTarget(items, 799))
        // "show 698 played songs" draws from the top.
        assertEquals((0 until 100).toList(), entries(roomQueueWindow(q, items, ROOM_QUEUE_ROWS_STEP, earlier = true)))
        // Fewer than 20 played: nothing folds.
        val early = queueOf(300, playing = 19)
        assertEquals(0, entries(roomQueueWindow(RoomQueue(items = early), early, ROOM_QUEUE_ROWS_STEP, false)).first())
    }

    @Test fun theUpNextHeadSitsBeforeTheFirstPickOnAnyPage() {
        // Picks start at 150: past the first page, the head waits at the end.
        val items = queueOf(400, playing = 0, picksFrom = 150)
        val q = RoomQueue(roomId = "office", items = items, sessionId = "s1", autoplay = true)
        val first = roomQueueWindow(q, items, ROOM_QUEUE_ROWS_STEP, earlier = false)
        assertEquals(listOf(RoomQueueLine.ShowMore(300), RoomQueueLine.UpNextHead), first.takeLast(2))
        assertEquals(1, first.count { it == RoomQueueLine.UpNextHead })
        // Once the pick is drawn, the head is right before it.
        val more = roomQueueWindow(q, items, 2 * ROOM_QUEUE_ROWS_STEP, earlier = false)
        val at = more.indexOf(RoomQueueLine.UpNextHead)
        assertEquals(RoomQueueLine.Entry(149), more[at - 1])
        assertEquals(RoomQueueLine.Entry(150), more[at + 1])
        assertEquals(1, more.count { it == RoomQueueLine.UpNextHead })
        // No session: no head at all.
        assertTrue(roomQueueWindow(q.copy(sessionId = null), items, 200, false).none { it == RoomQueueLine.UpNextHead })
        // And the window matches the whole-list lines once everything is drawn.
        assertEquals(roomQueueLines(q, items), roomQueueWindow(q, items, 400, earlier = false))
    }

    @Test fun pickingAnotherRoomStartsItsListAtTheTop() {
        val sel = RoomQueueSelection("office")
        sel.showMore(); sel.showMore(); sel.earlier = true
        assertEquals(300, sel.shown)
        sel.room = "office"            // the same room: kept
        assertEquals(300, sel.shown)
        sel.room = "den"
        assertEquals(ROOM_QUEUE_ROWS_STEP, sel.shown)
        assertFalse(sel.earlier)
    }
}
