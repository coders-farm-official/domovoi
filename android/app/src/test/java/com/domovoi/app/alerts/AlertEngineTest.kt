package com.domovoi.app.alerts

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
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
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The two alert paths end to end, against a fake web backend, a real
 * DataStore file and fake notification/alarm sinks:
 *
 *  A2  the live path then the alarm, and the alarm then the live path, post
 *      one timer once; the first-run and 30-minute catch-up rules as the
 *      server's reads drive them
 *  A6  the alarm's confirm step through real HTTP answers (404, 503, a
 *      cancel, a fire that lands between two reads)
 *  A8  the catch-up path string
 *  plus the mirror's sync, server switch, re-arm and notifications-off rules.
 */
class AlertEngineTest {
    private lateinit var server: MockWebServer
    private lateinit var api: ApiClient
    private lateinit var dir: File
    private lateinit var dsScope: CoroutineScope
    private lateinit var store: AlertStore
    private val sink = FakeSink()
    private val alarms = FakeAlarms()
    private var nowMs = Instant.parse("2026-09-30T15:10:00Z").toEpochMilli()
    private lateinit var url: String
    private lateinit var engine: AlertEngine

    /** path -> queued answers; the last one repeats. Missing -> 404. */
    private val answers = HashMap<String, ArrayDeque<MockResponse>>()
    private val paths = CopyOnWriteArrayList<String>()

    private fun answer(path: String, vararg bodies: String, code: Int = 200) {
        answers[path] = ArrayDeque(bodies.map { MockResponse().setResponseCode(code).setBody(it) })
    }

    private fun iso(ms: Long) = Instant.ofEpochMilli(ms).toString()

    @Before fun up() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path ?: ""
                paths += path
                val q = answers[path] ?: return MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                return if (q.size > 1) q.removeFirst() else q.first()
            }
        }
        server.start()
        url = server.url("/").toString().trimEnd('/')
        api = ApiClient(baseUrlProvider = { url }, deviceTokenProvider = { "household-token-1234" })
        dir = Files.createTempDirectory("alerts-test").toFile()
        dsScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        store = AlertStore(
            PreferenceDataStoreFactory.create(scope = dsScope, produceFile = { File(dir, "alerts.preferences_pb") }),
        )
        engine = AlertEngine(api, store, sink, alarms, serverUrl = { url }, clock = { nowMs })
    }

    @After fun down() {
        server.shutdown()
        dsScope.cancel()
        dir.deleteRecursively()
    }

    private val key get() = serverKey(url)

    private fun fireJson(id: Long, timerId: Long, firedAt: Long, summary: String = "heard in garage", kind: String = "timer") =
        """{"id":$id,"timer_id":$timerId,"kind":"$kind","is_reminder":${kind == "reminder"},"label":"pasta",
           "message":${if (kind == "reminder") "\"call mom\"" else "null"},"masked":false,"room_id":"garage",
           "created_at":"${iso(firedAt - 600_000)}","due_at":"${iso(firedAt)}","fired_at":"${iso(firedAt)}",
           "heard_in":["garage"],"summary":"$summary","deliveries":[{"room_id":"garage","is_origin":true,"outcome":"spoken"}]}"""

    private fun timersJson(serverNow: Long, vararg timers: Pair<Long, Long>) =
        """{"server_now":"${iso(serverNow)}","timers":[${timers.joinToString(",") { (id, exp) ->
            """{"id":$id,"expires_at":"${iso(exp)}","created_at":"${iso(exp - 600_000)}","label":"pasta","message":null,"room_id":"garage","is_reminder":false}"""
        }}],"fires":[]}"""

    private fun push(vararg fires: String) = runBlocking {
        engine.onFiresEvent(DomovoiJson.parseToJsonElement("[${fires.joinToString(",")}]"))
    }

    private fun loud() = sink.posts.filter { !it.silent }

    // ---- A2: one timer, one post, whichever path is first -------------------

    @Test fun theLivePathThenTheAlarmPostsOnce() = runBlocking {
        push(fireJson(42, 17, nowMs - 1_000))
        assertEquals(1, loud().size)
        val p = loud().single()
        assertEquals(key, p.serverKey)
        assertEquals("Timer done · garage", p.content.title)
        assertEquals("pasta", p.content.text)
        assertEquals("heard in garage", p.content.subText)

        engine.onAlarm(17, key)
        assertEquals("the alarm stays quiet", 1, sink.posts.size)
        assertEquals("...without even asking the server", emptyList<String>(), paths.toList())
    }

    @Test fun theAlarmThenTheLivePathPostsOnceAndTheLiveWordRefreshesIt() = runBlocking {
        answer("/api/timers", timersJson(nowMs, 17L to nowMs + 600_000))
        engine.syncMirror()
        answers.clear() // the server goes away: every read now 404s

        nowMs += 600_000
        engine.onAlarm(17, key)
        assertEquals(1, loud().size)
        assertEquals("couldn't reach Domovoi to confirm", loud().single().content.subText)
        assertEquals("pasta", loud().single().content.text)

        push(fireJson(42, 17, nowMs, summary = "heard in garage, kitchen"))
        assertEquals("no second alert", 1, loud().size)
        val refresh = sink.posts.last()
        assertTrue(refresh.silent)
        assertEquals("heard in garage, kitchen", refresh.content.subText)
    }

    @Test fun aDismissedNotificationIsNeverPostedAgain() = runBlocking {
        push(fireJson(42, 17, nowMs - 1_000, summary = "announcing…"))
        sink.active.clear() // the user swiped it away
        push(fireJson(42, 17, nowMs - 1_000, summary = "heard in garage"))
        assertEquals(1, sink.posts.size)
    }

    @Test fun aPushThatChangesNothingRepostsNothing() = runBlocking {
        push(fireJson(42, 17, nowMs - 1_000))
        push(fireJson(42, 17, nowMs - 1_000))
        assertEquals(1, sink.posts.size)
    }

    @Test fun aFirstPushPostsOnlyTheLastTwoMinutes() = runBlocking {
        push(fireJson(40, 15, nowMs - 30 * 60_000), fireJson(41, 16, nowMs - 60_000))
        assertEquals(listOf(16L), loud().map { it.content.timerId })
        assertEquals(41L, store.seen(key))
        // Later pushes post whatever is new.
        push(fireJson(41, 16, nowMs - 60_000), fireJson(43, 18, nowMs - 500))
        assertEquals(listOf(16L, 18L), loud().map { it.content.timerId })
    }

    // ---- A8 / A2: catch-up --------------------------------------------------------

    @Test fun catchUpAsksSinceTheLastSeenFireAndPostsTheLastHalfHour() = runBlocking {
        store.setSeen(key, 5)
        answer(
            "/api/timers/fires?since_id=5&limit=50",
            """{"server_now":"${iso(nowMs)}","fires":[${fireJson(6, 20, nowMs - 40 * 60_000)},${fireJson(7, 21, nowMs - 60_000)}]}""",
        )
        engine.catchUp()
        assertEquals(listOf("/api/timers/fires?since_id=5&limit=50"), paths.toList())
        assertEquals(listOf(21L), loud().map { it.content.timerId })
        assertEquals(7L, store.seen(key))
    }

    @Test fun aServersFirstCatchUpReadsTheNewestAndPostsTheLastTwoMinutes() = runBlocking {
        answer(
            "/api/timers/fires?limit=50",
            """{"server_now":"${iso(nowMs)}","fires":[${fireJson(9, 30, nowMs - 30_000)},${fireJson(8, 29, nowMs - 5 * 60_000)}]}""",
        )
        engine.catchUp()
        assertEquals(listOf("/api/timers/fires?limit=50"), paths.toList())
        assertEquals(listOf(30L), loud().map { it.content.timerId })
        assertEquals(9L, store.seen(key))
    }

    @Test fun catchUpFromAnOlderServerOrOneWithoutTheLedgerDoesNothing() = runBlocking {
        engine.catchUp() // 404
        answer("/api/timers/fires?limit=50", """{"detail":"timer fire history needs database migration V018 — run Flyway"}""", code = 503)
        engine.catchUp()
        assertEquals(emptyList<Post>(), sink.posts.toList())
        assertNull(store.seen(key))
    }

    @Test fun catchUpUsesTheServersClockNotThePhones() = runBlocking {
        store.setSeen(key, 1)
        // This phone's clock is an hour fast; the server says it is 15:10.
        val serverNow = nowMs
        nowMs += 60 * 60_000
        answer(
            "/api/timers/fires?since_id=1&limit=50",
            """{"server_now":"${iso(serverNow)}","fires":[${fireJson(2, 5, serverNow - 60_000)}]}""",
        )
        engine.catchUp()
        assertEquals(1, loud().size)
    }

    // ---- another history on the same address ------------------------------------------

    @Test fun aHistoryBehindTheRememberedIdStartsOverAsAFirstRun() = runBlocking {
        // This phone remembers fire 100 of a database that was since rebuilt;
        // the new one is at fire 5, and timer 17 was posted from the old one.
        store.setSeen(key, 100, iso(nowMs - 60 * 60_000))
        assertTrue(store.markAlerted(key, 17, nowMs - 60 * 60_000))
        answer("/api/timers/fires?since_id=100&limit=50", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers/fires?limit=1", """{"server_now":"${iso(nowMs)}","fires":[${fireJson(5, 17, nowMs - 30_000)}]}""")
        answer(
            "/api/timers/fires?limit=50",
            """{"server_now":"${iso(nowMs)}","fires":[${fireJson(5, 17, nowMs - 30_000)},${fireJson(4, 16, nowMs - 5 * 60_000)}]}""",
        )
        engine.catchUp()
        assertEquals(
            listOf("/api/timers/fires?since_id=100&limit=50", "/api/timers/fires?limit=1", "/api/timers/fires?limit=50"),
            paths.toList(),
        )
        // A first run: the 30 s old fire posts (its timer id collides with
        // the old history's 17, whose "already posted" entry is forgotten).
        assertEquals(listOf(17L), loud().map { it.content.timerId })
        assertEquals(5L, store.seen(key))
        assertEquals(iso(nowMs - 30_000), store.seenAt(key))
    }

    @Test fun anEmptyHistoryBehindTheRememberedIdStartsOverToo() = runBlocking {
        store.setSeen(key, 100, iso(nowMs - 60 * 60_000))
        answer("/api/timers/fires?since_id=100&limit=50", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers/fires?limit=1", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers/fires?limit=50", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        engine.catchUp()
        assertEquals(0L, store.seen(key))
    }

    @Test fun anEmptyTenMinuteViewIsNotAnotherHistory() = runBlocking {
        // A phone whose token the server no longer takes gets the open view
        // (rule F1: the last 10 minutes, `window_sec` 600). Quiet for longer
        // than that, its answer is empty; that must not start anything over.
        store.setSeen(key, 100, iso(nowMs - 60 * 60_000))
        assertTrue(store.markAlerted(key, 17, nowMs - 60 * 60_000))
        answer("/api/timers/fires?since_id=100&limit=50", """{"server_now":"${iso(nowMs)}","fires":[],"window_sec":600}""")
        answer("/api/timers/fires?limit=1", """{"server_now":"${iso(nowMs)}","fires":[],"window_sec":600}""")
        engine.catchUp()
        assertEquals(listOf("/api/timers/fires?since_id=100&limit=50", "/api/timers/fires?limit=1"), paths.toList())
        assertEquals(100L, store.seen(key))
        assertEquals(iso(nowMs - 60 * 60_000), store.seenAt(key))
        assertEquals(emptyList<Post>(), sink.posts.toList())
    }

    @Test fun aConsistentHistoryIsLeftAlone() = runBlocking {
        store.setSeen(key, 7, iso(nowMs - 60_000))
        answer("/api/timers/fires?since_id=7&limit=50", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers/fires?limit=1", """{"server_now":"${iso(nowMs)}","fires":[${fireJson(7, 21, nowMs - 60_000)}]}""")
        engine.catchUp()
        assertEquals(listOf("/api/timers/fires?since_id=7&limit=50", "/api/timers/fires?limit=1"), paths.toList())
        assertEquals(7L, store.seen(key))
    }

    @Test fun aPushThatWentOffAfterTheRememberedFireStartsOver() = runBlocking {
        push(fireJson(40, 15, nowMs - 60_000))
        assertEquals(40L, store.seen(key))
        // The database was rebuilt under a running app: fire 3 is newer than 40.
        nowMs += 120_000
        push(fireJson(3, 15, nowMs - 5_000))
        assertEquals(listOf(15L, 15L), loud().map { it.content.timerId })
        assertEquals(3L, store.seen(key))
        // An old fire pushed again (a summary update) starts nothing over.
        push(fireJson(2, 14, nowMs - 60 * 60_000))
        assertEquals(3L, store.seen(key))
        assertEquals(2, loud().size)
    }

    @Test fun historyRulesArePure() {
        fun f(id: Long, at: Long) = DomovoiJson.decodeFromString(TimerFire.serializer(), fireJson(id, id, at))
        assertFalse(historyRestarted(listOf(f(3, nowMs)), null, nowMs - 1))
        assertFalse(historyRestarted(listOf(f(3, nowMs)), 40, null))
        assertFalse(historyRestarted(listOf(f(41, nowMs)), 40, nowMs - 1))
        assertFalse(historyRestarted(listOf(f(40, nowMs - 1)), 40, nowMs - 1))
        assertTrue(historyRestarted(listOf(f(3, nowMs)), 40, nowMs - 1))
        assertTrue(historyBehind(null, 5))
        assertFalse(historyBehind(null, 0))
        assertTrue(historyBehind(f(4, nowMs), 5))
        assertFalse(historyBehind(f(5, nowMs), 5))
        // The open view (rule F1) reaches back 10 minutes: empty is no verdict,
        // but a fire in it still dates the history.
        assertFalse(historyBehind(null, 5, windowed = true))
        assertTrue(historyBehind(f(4, nowMs), 5, windowed = true))
        assertFalse(historyBehind(f(6, nowMs), 5, windowed = true))
        assertEquals(listOf("b|1|5"), forgetServer(listOf("a|1|5", "b|1|5", "a|2|6"), "a"))
    }

    // ---- notifications off ------------------------------------------------------------

    @Test fun withNotificationsOffNothingPostsAndNothingStaysArmed() = runBlocking {
        answer("/api/timers", timersJson(nowMs, 1L to nowMs + 60_000))
        engine.syncMirror()
        assertEquals(setOf(1L), alarms.armed.keys)

        sink.enabled = false
        push(fireJson(42, 17, nowMs - 1_000))
        assertEquals(emptyList<Post>(), sink.posts.toList())
        engine.syncMirror()
        assertEquals(emptyMap<Long, MirrorAlarm>(), alarms.armed.toMap())
        assertEquals(emptyList<MirrorAlarm>(), store.mirror().alarms)
        engine.onAlarm(1, key)
        assertEquals(emptyList<Post>(), sink.posts.toList())
    }

    // ---- the mirror ---------------------------------------------------------------------

    @Test fun theMirrorArmsNewTimersAndDisarmsGoneOnes() = runBlocking {
        // This phone runs 3 minutes slow.
        answer("/api/timers", timersJson(nowMs + 180_000, 1L to nowMs + 180_000 + 600_000, 2L to nowMs + 180_000 + 1_200_000))
        engine.syncMirror()
        assertEquals(setOf(1L, 2L), alarms.armed.keys)
        assertEquals(nowMs + 600_000, alarms.armed.getValue(1).trigger_at_ms)
        assertEquals(key, store.mirror().serverKey)

        answer("/api/timers", timersJson(nowMs + 180_000, 2L to nowMs + 180_000 + 1_200_000))
        engine.syncMirror()
        assertEquals(setOf(2L), alarms.armed.keys)
        assertEquals(listOf(1L), alarms.cancelled.toList())
    }

    @Test fun anUnreachableServerLeavesTheAlarmsArmed() = runBlocking {
        answer("/api/timers", timersJson(nowMs, 1L to nowMs + 600_000))
        engine.syncMirror()
        answers.clear()
        engine.syncMirror()
        assertEquals(setOf(1L), alarms.armed.keys)
        assertEquals(1, store.mirror().alarms.size)
    }

    @Test fun anotherServerTakesNoneOfTheOldOnesAlarms() = runBlocking {
        answer("/api/timers", timersJson(nowMs, 1L to nowMs + 600_000))
        engine.syncMirror()
        val oldKey = key
        url = "$url/"  // the same address normalises to the same server...
        assertEquals(oldKey, key)
        url = server.url("/other").toString().trimEnd('/')
        answer("/other/api/timers", timersJson(nowMs, 1L to nowMs + 900_000))
        engine.clearMirror()
        assertEquals(listOf(1L), alarms.cancelled.toList())
        engine.syncMirror()
        assertEquals(key, store.mirror().serverKey)
        assertEquals(nowMs + 900_000, alarms.armed.getValue(1).trigger_at_ms)
    }

    /** A force-stop cancels every alarm the app set and leaves the stored
     *  mirror listing them; the sync alone would arm only new or moved
     *  timers. A cold start re-arms first (TimerAlerts.start). */
    @Test fun aForceStopsLostAlarmsAreReArmedAtTheNextStart() = runBlocking {
        answer("/api/timers", timersJson(nowMs, 1L to nowMs + 600_000, 2L to nowMs + 1_200_000))
        engine.syncMirror()
        assertEquals(setOf(1L, 2L), alarms.armed.keys)
        alarms.armed.clear()                 // Settings > Force stop
        engine.syncMirror()
        assertEquals("the sync alone arms nothing it thinks is armed", emptySet<Long>(), alarms.armed.keys)
        engine.rearm()
        engine.syncMirror()
        assertEquals(setOf(1L, 2L), alarms.armed.keys)
    }

    /** The order inside the start's sync (arm the chain, re-arm, catch up,
     *  re-mirror) is TimerSyncTest.theStartArmsTheChainThenReArms...; this
     *  pins that start() runs it. */
    @Test fun theStartRunsTheReArmingSyncAndArmsTheChain() {
        val src = listOf(
            File("src/main/java/com/domovoi/app/alerts/TimerAlerts.kt"),
            File("app/src/main/java/com/domovoi/app/alerts/TimerAlerts.kt"),
        ).first { it.isFile }.readText()
        val start = src.substring(src.indexOf("fun start()"), src.indexOf("fun onAppResumed()"))
        assertTrue("start() arms the chain, re-arms the stored mirror, then syncs", "sync.onStart()" in start)
    }

    @Test fun aRebootReArmsOnlyWhatIsStillAhead() = runBlocking {
        store.setMirror(MirrorBook(key, listOf(MirrorAlarm(1, trigger_at_ms = nowMs - 1), MirrorAlarm(2, trigger_at_ms = nowMs + 60_000))))
        engine.rearm()
        assertEquals(setOf(2L), alarms.armed.keys)
        assertEquals(listOf(2L), store.mirror().alarms.map { it.timer_id })
    }

    // ---- A6: a ringing alarm asks the server ---------------------------------------------

    private fun mirrorOne(id: Long = 17, due: Long = nowMs) = runBlocking {
        store.setMirror(
            MirrorBook(
                key,
                listOf(MirrorAlarm(id, "reminder", "call mom", "call mom", "garage", iso(due - 600_000), iso(due), due)),
            ),
        )
    }

    @Test fun aRecordedFireIsPostedWithWhereItWasHeard() = runBlocking {
        mirrorOne()
        answer("/api/timers/fires?timer_id=17&limit=1", """{"server_now":"${iso(nowMs)}","fires":[${fireJson(42, 17, nowMs, kind = "reminder")}]}""")
        engine.onAlarm(17, key)
        val p = loud().single()
        assertEquals("Reminder · garage", p.content.title)
        assertEquals("call mom", p.content.text)
        assertEquals("heard in garage", p.content.subText)
        assertEquals(emptyList<MirrorAlarm>(), store.mirror().alarms)
    }

    @Test fun stillDueOnTheServerRingsGoingOffNow() = runBlocking {
        mirrorOne()
        answer("/api/timers/fires?timer_id=17&limit=1", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers", timersJson(nowMs, 17L to nowMs + 1_000))
        engine.onAlarm(17, key)
        assertEquals("going off now", loud().single().content.subText)
    }

    @Test fun laterOnTheServerIsReArmedNotPosted() = runBlocking {
        mirrorOne()
        answer("/api/timers/fires?timer_id=17&limit=1", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers", timersJson(nowMs, 17L to nowMs + 300_000))
        engine.onAlarm(17, key)
        assertEquals(emptyList<Post>(), sink.posts.toList())
        assertEquals(nowMs + 300_000, alarms.armed.getValue(17).trigger_at_ms)
        assertEquals(nowMs + 300_000, store.mirror().alarms.single().trigger_at_ms)
        assertFalse(store.wasAlerted(key, 17))
    }

    @Test fun aTimerCancelledWhileThePhoneWasAwayStaysQuiet() = runBlocking {
        mirrorOne()
        answer("/api/timers/fires?timer_id=17&limit=1", """{"server_now":"${iso(nowMs)}","fires":[]}""")
        answer("/api/timers", timersJson(nowMs))
        engine.onAlarm(17, key)
        assertEquals(emptyList<Post>(), sink.posts.toList())
        assertEquals(emptyList<MirrorAlarm>(), store.mirror().alarms)
        // It read the fire again before concluding "cancelled".
        assertEquals(
            listOf("/api/timers/fires?timer_id=17&limit=1", "/api/timers", "/api/timers/fires?timer_id=17&limit=1"),
            paths.toList(),
        )
    }

    @Test fun aTimerThatFiresBetweenTheReadsIsNotMistakenForACancel() = runBlocking {
        mirrorOne()
        answer(
            "/api/timers/fires?timer_id=17&limit=1",
            """{"server_now":"${iso(nowMs)}","fires":[]}""",
            """{"server_now":"${iso(nowMs)}","fires":[${fireJson(42, 17, nowMs, kind = "reminder")}]}""",
        )
        answer("/api/timers", timersJson(nowMs))
        engine.onAlarm(17, key)
        assertEquals("heard in garage", loud().single().content.subText)
    }

    @Test fun anOlderServerOrOneWithoutTheLedgerRingsUnconfirmed() = runBlocking {
        mirrorOne()
        engine.onAlarm(17, key) // 404
        assertEquals("couldn't reach Domovoi to confirm", loud().single().content.subText)
        assertEquals("call mom", loud().single().content.text)

        mirrorOne(id = 18)
        answer("/api/timers/fires?timer_id=18&limit=1", """{"detail":"timer fire history needs database migration V018 — run Flyway"}""", code = 503)
        engine.onAlarm(18, key)
        assertEquals(listOf(SUB_UNCONFIRMED, SUB_UNCONFIRMED), loud().map { it.content.subText })
    }

    @Test fun aSharedScreenRingsWithTheTitleAlone() = runBlocking {
        sink.sharedScreen = true
        mirrorOne()
        engine.onAlarm(17, key)
        val p = loud().single()
        assertEquals("Reminder · garage", p.content.title)
        assertNull(p.content.text)
        assertNull(p.content.subText)
    }

    @Test fun anAlarmForAnotherServerIsIgnored() = runBlocking {
        mirrorOne()
        engine.onAlarm(17, "00000000")
        assertEquals(emptyList<Post>(), sink.posts.toList())
        assertEquals(emptyList<String>(), paths.toList())
    }

    // ---- fakes -------------------------------------------------------------------------

    data class Post(val serverKey: String, val content: AlertContent, val silent: Boolean)

    class FakeSink : AlertSink {
        var enabled = true
        var sharedScreen = false
        val posts = CopyOnWriteArrayList<Post>()
        val active = HashSet<Pair<String, Long>>()
        override fun canPost() = enabled
        override fun shared() = sharedScreen
        override fun post(serverKey: String, content: AlertContent, silent: Boolean) {
            posts += Post(serverKey, content, silent)
            active += serverKey to content.timerId
        }
        override fun isActive(serverKey: String, timerId: Long) = (serverKey to timerId) in active
    }

    class FakeAlarms : AlarmSink {
        val armed = LinkedHashMap<Long, MirrorAlarm>()
        val cancelled = CopyOnWriteArrayList<Long>()
        override fun schedule(serverKey: String, alarm: MirrorAlarm) {
            armed[alarm.timer_id] = alarm
        }
        override fun cancel(timerId: Long) {
            armed.remove(timerId)
            cancelled += timerId
        }
    }
}
