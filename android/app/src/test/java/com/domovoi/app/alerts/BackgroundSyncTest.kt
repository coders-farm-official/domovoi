package com.domovoi.app.alerts

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DEVICE_TOKEN_HEADER
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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.time.Instant
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The background sync's tick end to end: the real AlertEngine behind
 * TimerSync, a fake web backend, a real DataStore file and fake
 * notification/alarm sinks. The phone is "in the background": nothing
 * arrives on the live socket; only the ticks (and the timer alarms) run.
 *
 *  - re-mirroring: a timer set by voice while the app was closed gets its
 *    alarm at the next tick; one cancelled meanwhile loses it
 *  - catch-up: a fire the phone missed posts once, under the 30-minute rule,
 *    and the live path and the alarm path stay quiet about it afterwards
 *    (and it about theirs)
 *  - boot: the mirror's future alarms come back at once, the rest at the
 *    first tick
 */
class BackgroundSyncTest {
    private lateinit var server: MockWebServer
    private lateinit var dir: File
    private lateinit var dsScope: CoroutineScope
    private lateinit var store: AlertStore
    private lateinit var url: String
    private lateinit var engine: AlertEngine
    private lateinit var sync: TimerSync
    private val sink = AlertEngineTest.FakeSink()
    private val alarms = AlertEngineTest.FakeAlarms()
    private var nowMs = Instant.parse("2026-09-30T15:10:00Z").toEpochMilli()
    private var elapsed = 90_000_000L
    private val chain = CopyOnWriteArrayList<Long>()

    /** path -> answer; missing -> 404 (an older server, or none at all). */
    private val answers = HashMap<String, MockResponse>()
    private val paths = CopyOnWriteArrayList<String>()
    private val tokens = CopyOnWriteArrayList<String?>()

    private fun answer(path: String, body: String, code: Int = 200) {
        answers[path] = MockResponse().setResponseCode(code).setBody(body)
    }

    private fun iso(ms: Long) = Instant.ofEpochMilli(ms).toString()

    @Before fun up() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                paths += request.path ?: ""
                tokens += request.getHeader(DEVICE_TOKEN_HEADER)
                return answers[request.path] ?: MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
            }
        }
        server.start()
        url = server.url("/").toString().trimEnd('/')
        val api = ApiClient(baseUrlProvider = { url }, deviceTokenProvider = { "household-token-1234" })
        dir = Files.createTempDirectory("sync-test").toFile()
        dsScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        store = AlertStore(
            PreferenceDataStoreFactory.create(scope = dsScope, produceFile = { File(dir, "alerts.preferences_pb") }),
        )
        engine = AlertEngine(api, store, sink, alarms, serverUrl = { url }, clock = { nowMs })
        sync = TimerSync(
            work = engine,
            alarm = object : SyncAlarm {
                override fun mode() = ChainMode.EXACT
                override fun schedule(atElapsedMs: Long, mode: ChainMode) { chain += atElapsedMs - elapsed }
                override fun cancel() { chain += -1L }
            },
            hasServer = { url.isNotBlank() },
            canPost = { sink.canPost() },
            mirrorTimes = { store.mirror().alarms.map { it.trigger_at_ms } },
            wall = { nowMs },
            elapsed = { elapsed },
        )
    }

    @After fun down() {
        server.shutdown()
        dsScope.cancel()
        dir.deleteRecursively()
    }

    private val key get() = serverKey(url)

    private fun fireJson(id: Long, timerId: Long, firedAt: Long, summary: String = "heard in garage") =
        """{"id":$id,"timer_id":$timerId,"kind":"timer","is_reminder":false,"label":"pasta","message":null,
           "masked":false,"room_id":"garage","created_at":"${iso(firedAt - 600_000)}","due_at":"${iso(firedAt)}",
           "fired_at":"${iso(firedAt)}","heard_in":["garage"],"summary":"$summary",
           "deliveries":[{"room_id":"garage","is_origin":true,"outcome":"spoken"}]}"""

    private fun firesJson(vararg fires: String) = """{"server_now":"${iso(nowMs)}","fires":[${fires.joinToString(",")}]}"""

    private fun timersJson(vararg timers: Pair<Long, Long>) =
        """{"server_now":"${iso(nowMs)}","timers":[${timers.joinToString(",") { (id, exp) ->
            """{"id":$id,"expires_at":"${iso(exp)}","created_at":"${iso(exp - 1_200_000)}","label":"pasta","message":null,"room_id":"garage","is_reminder":false}"""
        }}],"fires":[]}"""

    /** Nothing new past fire [seen]; the newest is [seen] itself. */
    private fun quietFires(seen: Long, firedAt: Long) {
        answer("/api/timers/fires?since_id=$seen&limit=50", firesJson())
        answer("/api/timers/fires?limit=1", firesJson(fireJson(seen, seen + 100, firedAt)))
    }

    private fun loud() = sink.posts.filter { !it.silent }

    // ---- re-mirroring -----------------------------------------------------------------

    @Test fun aTickArmsATimerSetWhileTheAppWasClosedAndDisarmsACancelledOne() = runBlocking {
        // While the app was open: timer 1 (the pasta, in 20 minutes) mirrored.
        answer("/api/timers", timersJson(1L to nowMs + 1_200_000))
        engine.syncMirror()
        store.setSeen(key, 5, iso(nowMs - 3_600_000))
        assertEquals(setOf(1L), alarms.armed.keys)

        // The app goes to the background. By voice: timer 1 cancelled in the
        // garage, timer 2 set in the kitchen for 25 minutes.
        nowMs += 5 * 60_000
        elapsed += 5 * 60_000
        answer("/api/timers", timersJson(2L to nowMs + 1_500_000))
        quietFires(5, nowMs - 3_600_000)
        paths.clear()

        assertEquals(TickResult.SYNCED, sync.onTick())
        assertEquals(setOf(2L), alarms.armed.keys)
        assertEquals(nowMs + 1_500_000, alarms.armed.getValue(2).trigger_at_ms)
        assertEquals(listOf(1L), alarms.cancelled.toList())
        assertEquals(listOf(2L), store.mirror().alarms.map { it.timer_id })
        assertEquals(emptyList<AlertEngineTest.Post>(), sink.posts.toList())
        assertEquals("the next tick first", listOf(SYNC_EXACT_LEAD_MS), chain.toList())
        assertEquals(
            listOf("/api/timers/fires?since_id=5&limit=50", "/api/timers/fires?limit=1", "/api/timers"),
            paths.toList(),
        )
        assertTrue("with the household token", tokens.all { it == "household-token-1234" })
    }

    @Test fun anUnreachableServerLeavesTheMirrorAsItWas() = runBlocking {
        answer("/api/timers", timersJson(1L to nowMs + 1_200_000))
        engine.syncMirror()
        answers.clear() // off the home network: every read fails
        assertEquals(TickResult.UNREACHABLE, sync.onTick())
        assertEquals(setOf(1L), alarms.armed.keys)
        assertEquals(listOf(1L), store.mirror().alarms.map { it.timer_id })
        assertEquals(listOf(SYNC_EXACT_LEAD_MS), chain.toList())
    }

    // ---- catch-up and dedupe ------------------------------------------------------------

    @Test fun aTickPostsAFireItMissedOnceAndTheOtherPathsStayQuiet() = runBlocking {
        store.setSeen(key, 5, iso(nowMs - 3_600_000))
        // A 3-minute timer set and done while the app slept: the phone never
        // knew it. Fire 6, 4 minutes ago.
        answer("/api/timers/fires?since_id=5&limit=50", firesJson(fireJson(6, 20, nowMs - 4 * 60_000)))
        answer("/api/timers", timersJson())
        assertEquals(TickResult.SYNCED, sync.onTick())
        val p = loud().single()
        assertEquals(20L, p.content.timerId)
        assertEquals("Timer done · garage", p.content.title)
        assertEquals("heard in garage", p.content.subText)
        assertEquals(nowMs - 4 * 60_000, p.content.whenMs)
        assertEquals(6L, store.seen(key))

        // The app is opened: the live path's push carries the same fire.
        engine.onFiresEvent(DomovoiJson.parseToJsonElement("[${fireJson(6, 20, nowMs - 4 * 60_000)}]"))
        // A stale alarm for the same timer rings (armed before a restore, say).
        engine.onAlarm(20, key)
        // And the next tick asks again.
        elapsed += SYNC_EXACT_LEAD_MS
        quietFires(6, nowMs - 4 * 60_000)
        assertEquals(TickResult.SYNCED, sync.onTick())
        assertEquals("one timer, one alert", 1, loud().size)
    }

    @Test fun aTickKeepsTheThirtyMinuteRule() = runBlocking {
        store.setSeen(key, 5, iso(nowMs - 3_600_000))
        answer(
            "/api/timers/fires?since_id=5&limit=50",
            firesJson(fireJson(6, 20, nowMs - 45 * 60_000), fireJson(7, 21, nowMs - 29 * 60_000)),
        )
        answer("/api/timers", timersJson())
        assertEquals(TickResult.SYNCED, sync.onTick())
        assertEquals("only the one under 30 minutes old", listOf(21L), loud().map { it.content.timerId })
        assertEquals(7L, store.seen(key))
    }

    @Test fun aFireTheAlarmAlreadyRangForIsNotPostedAgainByTheTick() = runBlocking {
        answer("/api/timers", timersJson(17L to nowMs + 600_000))
        engine.syncMirror()
        store.setSeen(key, 5, iso(nowMs - 3_600_000))
        // The alarm rings while the phone can't reach the server.
        answers.clear()
        nowMs += 600_000
        engine.onAlarm(17, key)
        assertEquals(SUB_UNCONFIRMED, loud().single().content.subText)

        // Back on the home network, the next tick finds the recorded fire.
        answer("/api/timers/fires?since_id=5&limit=50", firesJson(fireJson(6, 17, nowMs, summary = "heard in garage, kitchen")))
        answer("/api/timers", timersJson())
        elapsed += SYNC_EXACT_LEAD_MS
        assertEquals(TickResult.SYNCED, sync.onTick())
        assertEquals("no second alert", 1, loud().size)
        val refresh = sink.posts.last()
        assertTrue("the one still showing learns where it was heard", refresh.silent)
        assertEquals("heard in garage, kitchen", refresh.content.subText)
    }

    @Test fun withNotificationsOffATickAsksNothingAndDisarms() = runBlocking {
        answer("/api/timers", timersJson(1L to nowMs + 1_200_000))
        engine.syncMirror()
        paths.clear()
        sink.enabled = false
        assertEquals(TickResult.NOTIFICATIONS_OFF, sync.onTick())
        assertEquals(emptyList<String>(), paths.toList())
        assertEquals(emptyMap<Long, MirrorAlarm>(), alarms.armed.toMap())
        assertEquals(listOf(SYNC_EXACT_LEAD_MS), chain.toList())
    }

    // ---- boot ---------------------------------------------------------------------------

    @Test fun aBootReArmsWhatIsAheadAtOnceAndTheFirstTickFillsIn() = runBlocking {
        // Before the reboot: timers 1 (due during the reboot) and 2 mirrored.
        answer("/api/timers", timersJson(1L to nowMs + 60_000, 2L to nowMs + 3_600_000))
        engine.syncMirror()
        store.setSeen(key, 5, iso(nowMs - 3_600_000))
        alarms.armed.clear() // the reboot clears every alarm
        answers.clear()      // and Wi-Fi isn't up yet
        paths.clear()

        nowMs += 5 * 60_000
        elapsed = 30_000
        sync.onBoot()
        assertEquals("only what is still ahead", setOf(2L), alarms.armed.keys)
        assertEquals(listOf(2L), store.mirror().alarms.map { it.timer_id })
        assertEquals("the first tick soon", listOf(SYNC_AFTER_BOOT_MS), chain.toList())
        assertEquals("no network needed", emptyList<String>(), paths.toList())

        // The first tick: timer 1 went off during the reboot, timer 3 was set.
        elapsed += SYNC_AFTER_BOOT_MS
        answer("/api/timers/fires?since_id=5&limit=50", firesJson(fireJson(6, 1, nowMs - 4 * 60_000)))
        answer("/api/timers", timersJson(2L to nowMs + 3_300_000, 3L to nowMs + 900_000))
        assertEquals(TickResult.SYNCED, sync.onTick())
        assertEquals(listOf(1L), loud().map { it.content.timerId })
        assertEquals(setOf(2L, 3L), alarms.armed.keys)
    }
}
