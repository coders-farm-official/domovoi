package com.domovoi.app.player

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.DomovoiJson
import com.domovoi.app.net.decode
import com.domovoi.app.ui.screens.music.UpNextContext
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * The phone's Up next session (P8): what each kind of play starts, the
 * refill at five songs left, the picks a session takes back, the Autoplay
 * switch, the "for" selector, failures, a newer play or another server
 * winning over a late answer, the cast hand-back and the re-read.
 * MOBILE-HANDOFF-UPNEXT.md "Starts" and "Queue rules", line by line.
 */
class PhoneUpNextTest {

    // ── The rig: a queue, the app, and the server's answers ─────────────

    private class FakeHost : PhoneUpNext.Host {
        override var queue: List<PlayItem> = emptyList()
        override var index: Int = 0
        override var casting: Boolean = false
        var arrived = 0
        override fun retag(f: (PlayItem) -> PlayItem) { queue = queue.map(f) }
        override fun append(items: List<PlayItem>) { queue = queue + items }
        override fun removeWaiting(drop: (PlayItem) -> Boolean) {
            queue = queue.filterIndexed { i, it -> i <= index || !drop(it) }
        }
        override fun sessionArrived() { arrived++ }
    }

    private class FakeEnv : PhoneUpNext.Env {
        var server = "http://10.0.0.5:6369"
        var paired = true
        var autoplay = true
        var pick: String? = null
        var shared = false
        var picked: String? = "unset"
        override fun server(): String = server
        override fun paired(): Boolean = paired
        override fun deviceId() = "android-1a2b"
        override fun autoplay(): Boolean = autoplay
        override fun saveAutoplay(on: Boolean) { autoplay = on }
        override fun listenerPick(server: String) = pick
        override fun shared(server: String) = shared
        override fun setListener(personId: String?) { picked = personId }
    }

    /** Every call, as "method path body"; answers scripted per call kind. */
    private class FakeApi : UpNextApi {
        val calls = mutableListOf<Pair<String, JsonObject?>>()
        var startAnswer: () -> UpNextSession = { session("s1", picks = listOf(pick(1), pick(2))) }
        var nextAnswer: (Int) -> UpNextBatch = { UpNextBatch() }
        var feedbackAnswer: (Int, String) -> List<Int> = { seq, _ -> listOf(seq) }
        var patchAnswer: (JsonObject) -> UpNextSession? = { null }
        var readAnswer: () -> UpNextSession = { session("s1") }
        var moveAnswer: () -> UpNextSession? = { null }
        /** Run while the call is "on its way": a play or a server change meanwhile. */
        var during: () -> Unit = {}

        override suspend fun start(server: String, body: JsonObject): UpNextSession {
            calls += "start $server" to body; during(); return startAnswer()
        }
        override suspend fun next(server: String, id: String, haveThrough: Int): UpNextBatch {
            calls += "next $id $haveThrough" to null; during(); return nextAnswer(haveThrough)
        }
        override suspend fun feedback(server: String, id: String, seq: Int, kind: String): List<Int> {
            calls += "feedback $id $seq $kind" to null; return feedbackAnswer(seq, kind)
        }
        override suspend fun patch(server: String, id: String, body: JsonObject): UpNextSession? {
            calls += "patch $id" to body; return patchAnswer(body)
        }
        override suspend fun read(server: String, id: String): UpNextSession {
            calls += "read $id" to null; return readAnswer()
        }
        override suspend fun move(server: String, id: String, target: JsonObject): UpNextSession? {
            calls += "move $id" to target; return moveAnswer()
        }
        fun named(prefix: String) = calls.filter { it.first.startsWith(prefix) }
    }

    private val host = FakeHost()
    private val env = FakeEnv()
    private val api = FakeApi()
    private val upNext = PhoneUpNext(api, host, env, launch = { block -> runBlocking { block() } })

    companion object {
        fun pick(seq: Int, artist: String = "Artist $seq", reason: String = "same artist") = UpNextPick(
            seq = seq, trackId = 1000L + seq, title = "Pick $seq", artist = artist, album = "Album",
            durationSec = 200.0, reasonCode = "same_artist", reasonLabel = reason, status = "queued",
        )

        fun session(id: String, mode: String = "library", picks: List<UpNextPick> = emptyList(), autoplay: Boolean = true) =
            UpNextSession(
                id = id, target = UpNextTarget("device", deviceId = "android-1a2b"), mode = mode, autoplay = autoplay,
                picks = picks, haveThroughSeq = picks.maxOfOrNull { it.seq } ?: 0,
            )
    }

    private fun song(id: Long) = PlayItem.fromTrack(id, "Song $id", "Band", "Album", 180.0)

    /** The player playing [items] from [index], as a play started by [PhoneUpNext.newPlay]. */
    private fun playing(items: List<PlayItem>, index: Int = 0, origin: QueueOrigin = QueueOrigin.Explicit): Int {
        val g = upNext.newPlay()
        host.queue = items.map { it.copy(queueOrigin = origin, upNextGen = g) }
        host.index = index
        return g
    }

    private fun startBody(): JsonObject = api.named("start").last().second!!
    private fun waitingSeqs() = host.queue.filterIndexed { i, it -> UpNextRules.waiting(it, i, host.index) }.map { it.upNextSeq }

    // ── Starts ──────────────────────────────────────────────────────────

    @Test fun aSongTappedInAListStartsWithItsContext() {
        playing(listOf(song(7)))
        upNext.startFromList(7, UpNextContext("album", ref = "Band|Album", name = "Album"), listOf(6, 7, 8))
        val body = startBody()
        assertEquals("device", body["target"]!!.jsonObject["kind"]!!.jsonPrimitive.content)
        assertEquals("android-1a2b", body["target"]!!.jsonObject["device_id"]!!.jsonPrimitive.content)
        val start = body["start"]!!.jsonObject
        assertEquals(7L, start["track_id"]!!.jsonPrimitive.long)
        val ctx = start["context"]!!.jsonObject
        assertEquals("album", ctx["kind"]!!.jsonPrimitive.content)
        assertEquals("Band|Album", ctx["ref"]!!.jsonPrimitive.content)
        assertFalse("an album names itself by ref, not its songs", "track_ids" in ctx)
        assertFalse("whole_list" in start)
        assertTrue(body["autoplay"]!!.jsonPrimitive.boolean)
    }

    @Test fun anIdsListSendsItsSongsCappedAt500() {
        playing(listOf(song(1)))
        val ids = (1L..700L).toList()
        upNext.startFromList(1, UpNextContext("ids", name = "results for \"x\""), ids)
        val ctx = startBody()["start"]!!.jsonObject["context"]!!.jsonObject
        val sent = ctx["track_ids"]!!.jsonArray.map { it.jsonPrimitive.long }
        assertEquals(ids.take(500), sent)
    }

    @Test fun trackDetailsPlayIsLibraryMode() {
        playing(listOf(song(9)))
        upNext.startFromList(9, null, listOf(9))
        val start = startBody()["start"]!!.jsonObject
        assertEquals(setOf("track_id"), start.keys)
    }

    @Test fun aWholeListIsContinueModeFromItsFirstSong() {
        playing(listOf(song(3), song(4), song(5)), origin = QueueOrigin.WholeList)
        upNext.startWholeList(UpNextContext("playlist", ref = "12", name = "Road trip"), listOf(3, 4, 5))
        val start = startBody()["start"]!!.jsonObject
        assertTrue(start["whole_list"]!!.jsonPrimitive.boolean)
        assertEquals(3L, start["track_id"]!!.jsonPrimitive.long)
        assertEquals("playlist", start["context"]!!.jsonObject["kind"]!!.jsonPrimitive.content)
        assertEquals("12", start["context"]!!.jsonObject["ref"]!!.jsonPrimitive.content)
    }

    @Test fun aQueueAddedOneByOneStartsAtFiveLeftWhileAutoplayIsOn() {
        host.queue = (1L..7L).map(::song)
        host.index = 0
        upNext.check()
        assertTrue("six songs left: not yet", api.calls.isEmpty())
        host.index = 1
        upNext.check()
        val start = startBody()["start"]!!.jsonObject
        assertTrue(start["whole_list"]!!.jsonPrimitive.boolean)
        assertEquals(1L, start["track_id"]!!.jsonPrimitive.long)
        val ctx = start["context"]!!.jsonObject
        assertEquals("ids", ctx["kind"]!!.jsonPrimitive.content)
        assertEquals("this queue", ctx["name"]!!.jsonPrimitive.content)
        assertEquals((1L..7L).toList(), ctx["track_ids"]!!.jsonArray.map { it.jsonPrimitive.long })
        // The queue's songs are the session's now (their listens feed it).
        assertTrue(host.queue.take(7).all { it.sessionId == "s1" })
    }

    @Test fun aQueueAddedOneByOneStartsNothingWithAutoplayOff() {
        env.autoplay = false
        host.queue = listOf(song(1), song(2))
        upNext.check()
        assertTrue(api.calls.isEmpty())
    }

    @Test fun noStartForAQueueWithNoLibrarySongOrAnUnpairedApp() {
        host.queue = listOf(PlayItem.fromEpisode(5, "Ep", "Show", 1800.0, null, emptyList()))
        upNext.check()
        assertTrue(api.calls.isEmpty())
        host.queue = listOf(song(1))
        env.paired = false
        upNext.check()
        upNext.startFromList(1, null, listOf(1))
        assertTrue(api.calls.isEmpty())
        assertNull(upNext.state.value)
    }

    @Test fun theListenerFieldsGoWithTheStart() {
        env.pick = "4"
        playing(listOf(song(1)))
        upNext.startFromList(1, null, listOf(1))
        assertEquals(4L, startBody()["listener_person_id"]!!.jsonPrimitive.long)
        env.pick = "none"
        playing(listOf(song(1)))
        upNext.startFromList(1, null, listOf(1))
        assertTrue(startBody()["as_household"]!!.jsonPrimitive.boolean)
        assertFalse("listener_person_id" in startBody())
        env.pick = null
        playing(listOf(song(1)))
        upNext.startFromList(1, null, listOf(1))
        assertFalse("listener_person_id" in startBody())
        assertFalse("as_household" in startBody())
    }

    @Test fun theStartsSongsGetTheSessionAndThePicksJoinAsAuto() {
        playing(listOf(song(1)))
        host.queue = host.queue + song(50) // added by hand meanwhile: not the start's
        upNext.startFromList(1, null, listOf(1))
        assertEquals("s1", host.queue[0].sessionId)
        assertNull("a song added by hand is not the session's", host.queue[1].sessionId)
        val picks = host.queue.drop(2)
        assertEquals(listOf(1, 2), picks.map { it.upNextSeq })
        assertTrue(picks.all { it.queueOrigin == QueueOrigin.Auto && it.sessionId == "s1" })
        assertEquals("same artist", picks[0].reasonLabel)
        assertEquals(1, host.arrived)
        assertEquals(2, upNext.state.value!!.haveThrough)
    }

    // ── The refill ──────────────────────────────────────────────────────

    /** A session "s1" with [picks] waiting after the one song playing. The
     *  start's own refill check is held off (as while casting), so each test
     *  drives the refill itself. */
    private fun withSession(mode: String = "library", picks: Int = 3, autoplay: Boolean = true) {
        api.startAnswer = { session("s1", mode = mode, picks = (1..picks).map { pick(it) }, autoplay = autoplay) }
        playing(listOf(song(1)))
        host.casting = true
        upNext.startFromList(1, null, listOf(1))
        host.casting = false
        api.calls.clear()
    }

    @Test fun refillsAtFiveLeftWithTheHighestSeqHeld() {
        // Seven picks: the start's check found six left, and asked nothing.
        withSession(picks = 7)
        assertTrue(api.named("next").isEmpty())
        api.nextAnswer = { have -> UpNextBatch(picks = listOf(pick(have + 1), pick(have + 2))) }
        host.index = 2
        upNext.check()
        assertEquals("next s1 7", api.calls.first().first)
        assertEquals(9, upNext.state.value!!.haveThrough)
        assertEquals(listOf(3, 4, 5, 6, 7, 8, 9), waitingSeqs())
    }

    @Test fun aRefillDropsWhatTheSessionTookBackAndNeverAddsWhatItHolds() {
        withSession(picks = 4)
        // The answer repeats seq 4 (held) and takes back 3.
        api.nextAnswer = { UpNextBatch(picks = listOf(pick(4), pick(5)), removed = listOf(3)) }
        api.during = {}
        upNext.check()
        assertEquals(listOf(1, 2, 4, 5), waitingSeqs())
        assertEquals(5, upNext.state.value!!.haveThrough)
    }

    @Test fun aFailedRefillSaysSoAndRetriesWithTheSameNumberOnTheNextSong() {
        withSession(picks = 3)
        api.nextAnswer = { throw IOException("unreachable") }
        upNext.check()
        assertEquals(listOf("next s1 3"), api.calls.map { it.first })
        assertTrue(upNext.state.value!!.offline)
        assertEquals(UP_NEXT_OFFLINE, phoneUpNextLine(upNext.state.value, true, 3))
        // The same song again: never in a loop.
        upNext.check()
        assertEquals(1, api.named("next").size)
        // The next song: the same have_through_seq (the call is idempotent).
        api.nextAnswer = { UpNextBatch(picks = listOf(pick(4))) }
        host.index = 1
        upNext.check()
        assertEquals("next s1 3", api.named("next")[1].first)
        assertFalse(upNext.state.value!!.offline)
    }

    @Test fun anEmptyAnswerIsAskedAgainOnlyOnTheNextSong() {
        withSession(picks = 2)
        api.nextAnswer = { UpNextBatch() }
        upNext.check()
        upNext.check()
        assertEquals(1, api.named("next").size)
        host.index = 1
        upNext.check()
        assertEquals(2, api.named("next").size)
    }

    @Test fun anEndedSessionIsNotAskedAgain() {
        withSession(picks = 1)
        api.nextAnswer = { UpNextBatch(ended = true) }
        upNext.check()
        host.index = 1
        upNext.check()
        assertEquals(1, api.named("next").size)
    }

    @Test fun autoplayOffAsksNothingMoreInALibraryMixButAListPlaysOut() {
        withSession(mode = "library", picks = 1, autoplay = false)
        upNext.check()
        assertTrue(api.named("next").isEmpty())
        withSession(mode = "list", picks = 1, autoplay = false)
        api.nextAnswer = { UpNextBatch(picks = listOf(pick(2))) }
        upNext.check()
        assertEquals("next s1 1", api.named("next").first().first)
    }

    @Test fun aListThatPlayedOutTurnsIntoContinue() {
        withSession(mode = "list", picks = 1)
        api.nextAnswer = { UpNextBatch(picks = listOf(pick(2)), mode = "continue") }
        upNext.check()
        assertEquals("continue", upNext.state.value!!.mode)
    }

    @Test fun noRefillWhileCasting() {
        withSession(picks = 1)
        host.casting = true
        upNext.check()
        assertTrue(api.calls.isEmpty())
    }

    // ── Failures of a start ─────────────────────────────────────────────

    @Test fun aRefusedStartSaysNothingAndShowsNothing() {
        api.startAnswer = { throw ApiException(404, "404 Not Found: no such song") }
        playing(listOf(song(1)))
        upNext.startFromList(1, null, listOf(1))
        val u = upNext.state.value!!
        assertTrue(u.refused)
        assertFalse(phoneUpNextShown(true, host.queue, u, casting = false))
        // ...and is not tried again for this play.
        host.index = 0
        upNext.check()
        assertEquals(1, api.named("start").size)
    }

    @Test fun aStartTheServerFailedIsSaidAndTriedAgainOnTheNextSong() {
        api.startAnswer = { throw ApiException(503, "503 Service Unavailable") }
        playing(listOf(song(1), song(2)))
        upNext.startFromList(1, null, listOf(1))
        assertEquals(UP_NEXT_OFFLINE, phoneUpNextLine(upNext.state.value, true, 0))
        upNext.check() // notes the song it failed on
        upNext.check()
        assertEquals(1, api.named("start").size)
        api.startAnswer = { session("s2", picks = listOf(pick(1))) }
        host.index = 1
        upNext.check()
        assertEquals(2, api.named("start").size)
        assertEquals("s2", upNext.state.value!!.id)
        assertEquals("the retry is the same start", api.calls[0].second!!["start"], api.calls[1].second!!["start"])
        assertEquals("s2", host.queue[0].sessionId)
    }

    @Test fun aNetworkFailureIsOfflineToo() {
        api.startAnswer = { throw IOException("no route") }
        playing(listOf(song(1)))
        upNext.startFromList(1, null, listOf(1))
        assertTrue(upNext.state.value!!.offline)
        assertFalse(upNext.state.value!!.refused)
    }

    // ── A newer play, another server ────────────────────────────────────

    @Test fun aNewerPlayWinsOverALateAnswer() {
        playing(listOf(song(1)))
        api.during = { playing(listOf(song(2))) } // another play while the start is on its way
        upNext.startFromList(1, null, listOf(1))
        assertNull("the old start's answer was dropped", upNext.state.value)
        assertTrue(host.queue.none { it.queueOrigin == QueueOrigin.Auto })
        assertNull(host.queue[0].sessionId)
    }

    @Test fun anAnswerFromTheServerLeftIsDropped() {
        playing(listOf(song(1)))
        api.during = { env.server = "http://10.0.0.9:6369" }
        upNext.startFromList(1, null, listOf(1))
        assertTrue(host.queue.none { it.queueOrigin == QueueOrigin.Auto })
        assertNull(upNext.state.value!!.id)
    }

    @Test fun aRefillAnsweredAfterANewPlayAddsNothing() {
        withSession(picks = 1)
        api.nextAnswer = { UpNextBatch(picks = listOf(pick(2))) }
        api.during = { playing(listOf(song(9))) }
        upNext.check()
        assertTrue(host.queue.none { it.queueOrigin == QueueOrigin.Auto })
    }

    // ── What the person does ────────────────────────────────────────────

    @Test fun autoplayOffDropsTheWaitingPicksOfAMixAndTellsTheServer() = runBlocking {
        withSession(mode = "library", picks = 3)
        upNext.setAutoplay(false)
        assertTrue(host.queue.none { it.queueOrigin == QueueOrigin.Auto })
        assertFalse(env.autoplay)
        val (call, body) = api.named("patch").single()
        assertEquals("patch s1", call)
        assertFalse(body!!["autoplay"]!!.jsonPrimitive.boolean)
        assertEquals(UP_NEXT_AUTOPLAY_OFF, phoneUpNextLine(upNext.state.value, false, 0))
    }

    @Test fun autoplayOffKeepsAListsOwnPicks() = runBlocking {
        api.startAnswer = {
            UpNextSession(
                id = "s1", mode = "list", picks = listOf(pick(1), pick(2)), haveThroughSeq = 2,
                seed = com.domovoi.app.ui.screens.music.RoomUpNextSeed(list = com.domovoi.app.ui.screens.music.RoomUpNextList("playlist", "Road trip")),
            )
        }
        playing(listOf(song(1)))
        upNext.startFromList(1, UpNextContext("playlist", ref = "3", name = "Road trip"), listOf(1))
        upNext.setAutoplay(false)
        assertEquals(listOf(1, 2), waitingSeqs())
        assertEquals("autoplay is off — stops after the last song of Road trip", phoneUpNextLine(upNext.state.value, false, 2))
    }

    @Test fun lessLikeThisDropsTheArtistsPicks() = runBlocking {
        api.startAnswer = { session("s1", picks = listOf(pick(1, "A"), pick(2, "B"), pick(3, "A"))) }
        api.feedbackAnswer = { _, _ -> listOf(3) }
        playing(listOf(song(1)))
        upNext.startFromList(1, null, listOf(1))
        val gone = upNext.lessLikeThis(1)
        assertEquals("feedback s1 1 less_like_this", api.named("feedback").single().first)
        assertEquals(setOf(1, 3), gone.toSet())
        assertEquals(listOf(2), waitingSeqs())
    }

    @Test fun lessLikeThisOnASongThatIsNoPickSendsNothing() = runBlocking {
        withSession(picks = 1)
        assertTrue(upNext.lessLikeThis(0).isEmpty())
        assertTrue(api.calls.isEmpty())
    }

    @Test fun aRemovedPickIsToldAndWhatItTakesBackLeaves() {
        withSession(picks = 3)
        api.feedbackAnswer = { seq, _ -> listOf(seq, 3) }
        val gone = host.queue[1]
        host.queue = host.queue.filterIndexed { i, _ -> i != 1 }
        upNext.removed(gone)
        assertEquals("feedback s1 1 remove", api.named("feedback").single().first)
        assertEquals(listOf(2), waitingSeqs())
    }

    @Test fun theForSelectorPatchesTheListener() = runBlocking {
        withSession(picks = 1)
        api.patchAnswer = { session("s1").copy(person = UpNextPerson(4, "Sam")) }
        upNext.setListener(4)
        assertEquals("4", env.picked)
        assertEquals(4L, api.named("patch").last().second!!["listener_person_id"]!!.jsonPrimitive.long)
        assertEquals("Sam", upNext.state.value!!.person!!.name)
        upNext.setListener(null)
        assertNull(env.picked)
        assertEquals(JsonNull, api.named("patch").last().second!!["listener_person_id"])
    }

    // ── Casting ─────────────────────────────────────────────────────────

    @Test fun aCastCarriesTheSessionAutoplayAndListener() {
        withSession(picks = 1)
        env.autoplay = false
        env.pick = "4"
        val body = kotlinx.serialization.json.buildJsonObject { upNext.castFields(this) }
        assertEquals("s1", body["up_next_session_id"]!!.jsonPrimitive.content)
        assertFalse(body["autoplay"]!!.jsonPrimitive.boolean)
        assertEquals(4L, body["listener_person_id"]!!.jsonPrimitive.long)
    }

    @Test fun anEndedSessionIsNotCast() {
        withSession(picks = 1)
        api.nextAnswer = { UpNextBatch(ended = true) }
        upNext.check()
        val body = kotlinx.serialization.json.buildJsonObject { upNext.castFields(this) }
        assertFalse("up_next_session_id" in body)
        assertFalse("autoplay on is the server's default" , "autoplay" in body)
    }

    @Test fun handBackMovesTheSessionToThisDevice() {
        withSession(picks = 1)
        upNext.handBack()
        val (call, target) = api.named("move").single()
        assertEquals("move s1", call)
        assertEquals("device", target!!["kind"]!!.jsonPrimitive.content)
        assertEquals("android-1a2b", target["device_id"]!!.jsonPrimitive.content)
        assertEquals("s1", upNext.state.value!!.id)
    }

    @Test fun aHandBackTheRoomRefusedDropsTheSession() {
        for (status in listOf(409, 404)) {
            withSession(picks = 1)
            api.moveAnswer = { throw ApiException(status, "$status refused") }
            api.startAnswer = { session("s9") }
            upNext.handBack()
            // Dropped; the queue (near its end) starts a new one of its own.
            assertEquals("$status: dropped", "s9", upNext.state.value?.id)
            val ctx = startBody()["start"]!!.jsonObject["context"]!!.jsonObject
            assertEquals("this queue", ctx["name"]!!.jsonPrimitive.content)
        }
    }

    // ── The re-read ─────────────────────────────────────────────────────

    @Test fun aRereadReplacesTheWaitingPicksWithTheServers() {
        withSession(picks = 3)
        api.readAnswer = { session("s1", picks = listOf(pick(1), pick(3), pick(4))) }
        upNext.reread()
        assertEquals(listOf(1, 3, 4), waitingSeqs())
        assertEquals(4, upNext.state.value!!.haveThrough)
    }

    @Test fun aSessionTheServerForgotIsDroppedOnReread() {
        withSession(picks = 1)
        api.readAnswer = { throw ApiException(404, "404 gone") }
        api.startAnswer = { session("s9") }
        upNext.reread()
        assertEquals("dropped, and a new one for the queue", "s9", upNext.state.value?.id)
    }

    // ── The wire, as the server writes it ────────────────────────────────

    /** domovoi/up_next/service.py session_json + pick_json, field for field. */
    @Test fun decodesTheServersSession() {
        val json = """
            {"id":"5f0c2a7e-3b1d-4c55-9a0e-2f6a1b9d7c11",
             "target":{"kind":"device","device_id":"android-1a2b"},
             "mode":"list","autoplay":true,
             "person":{"id":4,"name":"Sam"},
             "seed":{"track_id":812,"title":"Harvest Moon","artist":"Neil Young","label":"Harvest Moon",
                     "list":{"kind":"playlist","name":"Road trip"}},
             "picks":[{"seq":1,"track_id":901,"title":"Old Man","artist":"Neil Young","album":"Harvest",
                       "duration_sec":203.4,"reason_code":"same_artist","reason_label":"same artist",
                       "status":"queued","mpd_song_id":null,"batch_no":1},
                      {"seq":2,"track_id":902,"title":"","artist":"","album":"","duration_sec":null,
                       "reason_code":"from_list","reason_label":"from Road trip","status":"queued",
                       "mpd_song_id":null,"batch_no":1}],
             "batch_no":1,"have_through_seq":2,"ended":false}
        """.trimIndent()
        val s = DomovoiJson.parseToJsonElement(json).decode<UpNextSession>()
        assertEquals("list", s.mode)
        assertEquals("Road trip", s.seed!!.list!!.name)
        assertEquals(2, s.haveThroughSeq)
        assertEquals("Sam", s.person!!.name)
        val items = s.picks.map { UpNextRules.pickItem(it, s.id) }
        assertEquals("Old Man", items[0].title)
        assertEquals("track", items[1].title)
        assertNull("an empty artist is none", items[1].artist)
        assertEquals("/api/music/library/902/audio", items[1].src)
        assertEquals("shuffled from Road trip", com.domovoi.app.ui.screens.music.upNextBasis(s.mode, s.seed))
    }

    /** web/backend/api/up_next.py next_batch's answer. */
    @Test fun decodesTheServersBatch() {
        val json = """{"batch_no":3,"picks":[{"seq":7,"track_id":5,"title":"T","artist":"A","album":"",
            "duration_sec":100,"reason_code":"wildcard","reason_label":"wildcard","status":"queued",
            "mpd_song_id":null,"batch_no":3}],"ended":false,"removed":[4,5],"mode":"continue"}"""
        val b = DomovoiJson.parseToJsonElement(json).decode<UpNextBatch>()
        assertEquals(listOf(4, 5), b.removed)
        assertEquals("continue", b.mode)
        assertEquals(7, b.picks.single().seq)
        assertEquals(listOf(3), HttpUpNextApi.removedOf(DomovoiJson.parseToJsonElement("""{"removed":[3]}""")))
    }

    // ── What the queue shows ────────────────────────────────────────────

    @Test fun theCountLeavesOutTheWaitingPicks() {
        withSession(picks = 3)
        host.index = 0
        assertEquals(1, UpNextRules.count(host.queue, 0))
        assertEquals(listOf(0), UpNextRules.explicitRows(host.queue, 0))
        assertEquals(listOf(1, 2, 3), UpNextRules.pickRows(host.queue, 0))
        // A pick that played (or plays) counts.
        assertEquals(3, UpNextRules.count(host.queue, 2))
    }

    @Test fun addToQueueLandsBeforeTheFirstPickStillToCome() {
        withSession(picks = 3)
        assertEquals(1, UpNextRules.addAt(host.queue, 0))
        // Past the picks already played.
        assertEquals(4, UpNextRules.addAt(host.queue, 3))
        assertEquals(1, UpNextRules.addAt(listOf(song(1)), 0))
    }

    @Test fun theSectionIsHiddenWhereItCantWork() {
        val lib = listOf(song(1))
        val pod = listOf(PlayItem.fromEpisode(5, "Ep", "Show", 1800.0, null, emptyList()))
        val phone = listOf(PlayItem.fromDeviceAudio(5, "Mine", null, null, 100.0, "content://x/5", null))
        assertTrue(phoneUpNextShown(true, lib, null, casting = false))
        assertFalse("unpaired", phoneUpNextShown(false, lib, null, casting = false))
        assertFalse("a podcast", phoneUpNextShown(true, pod, null, casting = false))
        assertFalse("a song on the phone", phoneUpNextShown(true, phone, null, casting = false))
        assertFalse("casting: the room's own", phoneUpNextShown(true, lib, null, casting = true))
        assertFalse(phoneUpNextForShown(1, UpNextState("s", 1, id = "x")))
        assertFalse(phoneUpNextForShown(2, UpNextState("s", 1)))
        assertTrue(phoneUpNextForShown(2, UpNextState("s", 1, id = "x")))
    }

    @Test fun theMutedLine() {
        assertEquals(UP_NEXT_FINDING, phoneUpNextLine(UpNextState("s", 1, loading = true), true, 0))
        assertEquals(UP_NEXT_JOIN_LATER, phoneUpNextLine(UpNextState("s", 1, id = "x"), true, 0))
        assertNull(phoneUpNextLine(UpNextState("s", 1, id = "x"), true, 2))
        assertEquals(UP_NEXT_AUTOPLAY_OFF, phoneUpNextLine(null, false, 0))
        assertEquals("autoplay: keep playing similar songs when the queue ends", UP_NEXT_AUTOPLAY_LABEL)
    }
}
