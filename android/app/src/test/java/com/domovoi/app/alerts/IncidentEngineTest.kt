package com.domovoi.app.alerts

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DomovoiJson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The container guard's engine end to end, against fake web backends, a
 * real DataStore file and a fake notification sink: first contact, no
 * second post for an incident (re-read, push, restart), a new incident
 * posts, a resolved one never does, a phone refused the list sees only the
 * summary and posts nothing (without touching the pairing screen), each
 * server keeps its own book, and the background tick's rules.
 */
class IncidentEngineTest {
    /** One fake web backend: path -> answer; missing -> 404. */
    private class Web {
        val server = MockWebServer()
        val answers = HashMap<String, MockResponse>()
        val paths = CopyOnWriteArrayList<String>()

        init {
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    val path = request.path ?: ""
                    paths += path
                    return answers[path] ?: MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                }
            }
            server.start()
        }

        val url: String get() = server.url("/").toString().trimEnd('/')

        fun answer(path: String, body: String, code: Int = 200) {
            answers[path] = MockResponse().setResponseCode(code).setBody(body)
        }

        fun list(body: String) = answer(IncidentsApi.INCIDENTS_PATH, body)
    }

    private lateinit var a: Web
    private lateinit var b: Web
    private lateinit var url: String
    private lateinit var api: ApiClient
    private lateinit var dir: File
    private lateinit var dsScope: CoroutineScope
    private lateinit var store: IncidentStore
    private val sink = FakeSink()
    private var now = 1_000_000L
    private lateinit var engine: IncidentEngine

    private fun newStore(): IncidentStore {
        dsScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        return IncidentStore(
            PreferenceDataStoreFactory.create(scope = dsScope, produceFile = { File(dir, "incidents.preferences_pb") }),
        )
    }

    private fun newEngine() = IncidentEngine(api, store, sink, serverUrl = { url }, elapsed = { now })

    @Before fun up() {
        a = Web()
        b = Web()
        url = a.url
        api = ApiClient(baseUrlProvider = { url }, deviceTokenProvider = { "household-token-1234" })
        dir = Files.createTempDirectory("incidents-test").toFile()
        store = newStore()
        engine = newEngine()
    }

    @After fun down() {
        a.server.shutdown()
        b.server.shutdown()
        dsScope.cancel()
        dir.deleteRecursively()
    }

    private fun incident(
        id: Long,
        cls: String = "failure",
        open: Boolean = true,
        acknowledged: Boolean = false,
        severity: String = "error",
    ) = """{"id":$id,"class":"$cls","kind":"${if (cls == "tamper") "image" else "stopped"}","severity":"$severity",
           "label":"helper $id","summary":"Incident $id.","owner":"core","container":"domovoi-c$id",
           "opened_at":"2026-10-11T14:${"%02d".format(id % 60)}:00+00:00","last_seen_at":"2026-10-11T14:09:00+00:00",
           "resolved_at":${if (open) "null" else "\"2026-10-11T14:10:00+00:00\""},
           "resolution":${if (open) "null" else "\"recovered\""},"acknowledged":$acknowledged,
           "acknowledged_at":${if (acknowledged) "\"2026-10-11T14:09:30+00:00\"" else "null"},
           "cleared_at":null,"open":$open,"freezes":${cls == "tamper" && open},
           "can_acknowledge":${cls == "failure" && open && !acknowledged},"can_clear":${cls == "tamper" && open}}"""

    /** A payload with the server's own banner for [incidents]. */
    private fun payload(vararg incidents: String): String {
        val parsed = parseIncidents(DomovoiJson.parseToJsonElement("""{"incidents":[${incidents.joinToString(",")}]}"""))!!
        val b = parsed.banner
        val tone = b.tone?.let { "\"$it\"" } ?: "null"
        return """{"incidents":[${incidents.joinToString(",")}],
                   "banner":{"show":${b.show},"tone":$tone,"text":"${b.text}","count":${b.count},
                             "incident_ids":${b.incidentIds},"frozen":${b.frozen}},
                   "frozen":${b.frozen},"attention":${b.show}}"""
    }

    private fun push(body: String) = runBlocking { engine.onEvent(DomovoiJson.parseToJsonElement(body)) }

    private fun postedIds() = sink.posts.map { it.content.incidentId }

    private val refusal = """{"detail":"household device token required (X-Device-Token)"}"""

    // ---- one notification per incident ------------------------------------------

    @Test fun firstContactPostsWhatTheBannerShowsAndRemembersIt() = runBlocking {
        a.list(
            payload(
                incident(12, cls = "tamper"),
                incident(11, acknowledged = true),
                incident(10),
                incident(9, open = false),
            ),
        )
        assertTrue(engine.refresh())
        assertEquals(listOf(10L, 12L), postedIds())
        val tamper = sink.posts.single { it.content.incidentId == 12L }
        assertEquals("Signs of tampering", tamper.content.title)
        assertEquals("Incident 12.", tamper.content.text)
        assertEquals(serverKey(a.url), tamper.serverKey)
        assertEquals("A container failed", sink.posts.single { it.content.incidentId == 10L }.content.title)

        val s = engine.state.value
        assertEquals(IncidentTier.HOUSEHOLD, s.tier)
        assertTrue(s.loaded)
        assertTrue(s.frozen)
        assertEquals(listOf(12L, 11L, 10L), s.open.map { it.id })
        assertEquals(listOf(9L), s.resolved.map { it.id })
        assertTrue(shellBanner(s, shared = false).show)
        assertEquals(listOf(IncidentsApi.INCIDENTS_PATH), a.paths.toList())
    }

    @Test fun aReReadOrAPushOfTheSameIncidentsPostsNothing() = runBlocking {
        val body = payload(incident(12, cls = "tamper"))
        a.list(body)
        engine.refresh()
        assertEquals(1, sink.posts.size)
        engine.refresh()
        push(body)
        engine.refresh()
        assertEquals("one notification per incident, ever", 1, sink.posts.size)
    }

    @Test fun aNewIncidentInAPushPostsAndOnlyIt() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        engine.refresh()
        push(payload(incident(13, severity = "warn"), incident(12, cls = "tamper")))
        assertEquals(listOf(12L, 13L), postedIds())
        assertEquals(listOf(13L, 12L), engine.state.value.open.map { it.id })
    }

    @Test fun aResolvedIncidentNeverPosts() = runBlocking {
        a.list(payload(incident(5, open = false), incident(4, cls = "tamper", open = false)))
        engine.refresh()
        assertEquals(2, engine.state.value.resolved.size)
        push(payload(incident(5, open = false)))
        assertEquals(emptyList<Long>(), postedIds())
        assertFalse(shellBanner(engine.state.value, shared = false).show)
        assertEquals("the push is the newest word", listOf(5L), engine.state.value.resolved.map { it.id })
    }

    @Test fun aRestartOfTheAppDoesNotRePost() = runBlocking {
        a.list(payload(incident(12, cls = "tamper"), incident(10)))
        engine.refresh()
        assertEquals(2, sink.posts.size)
        // A new process: the DataStore file read afresh, a new engine.
        dsScope.cancel()
        dsScope.coroutineContext[Job]!!.join()
        store = newStore()
        engine = newEngine()
        engine.refresh()
        assertEquals(2, sink.posts.size)
        // ...and what is new since still posts.
        a.list(payload(incident(13), incident(12, cls = "tamper"), incident(10)))
        engine.refresh()
        assertEquals(listOf(10L, 12L, 13L), postedIds())
    }

    @Test fun eachServerHasItsOwnBook() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        b.list(payload(incident(12, cls = "tamper")))
        engine.refresh()
        url = b.url
        engine.serverChanged()
        assertEquals("the old server's banner goes with it", IncidentTier.NONE, engine.state.value.tier)
        engine.refresh()
        assertEquals("another house's #12 is another incident", 2, sink.posts.size)
        assertEquals(listOf(serverKey(a.url), serverKey(b.url)), sink.posts.map { it.serverKey })
        url = a.url
        engine.serverChanged()
        engine.refresh()
        assertEquals("back home: already posted", 2, sink.posts.size)
    }

    @Test fun aPushForTheOldServerIsNotApplied() = runBlocking {
        val stale = payload(incident(3))
        url = ""
        engine.serverChanged()
        push(stale)
        assertEquals(emptyList<Long>(), postedIds())
        assertEquals(IncidentTier.NONE, engine.state.value.tier)
    }

    @Test fun aSharedScreenPostsOnlyThePublicWords() = runBlocking {
        sink.sharedScreen = true
        a.list(payload(incident(12, cls = "tamper")))
        engine.refresh()
        val c = sink.posts.single().content
        assertEquals(INCIDENT_PUBLIC_TITLE, c.title)
        assertNull(c.text)
        assertEquals(ADMIN_ATTENTION_TEXT, shellBanner(engine.state.value, shared = true).text)
    }

    @Test fun withNotificationsOffNothingPostsOrIsRememberedUntilTheyAreBack() = runBlocking {
        sink.enabled = false
        a.list(payload(incident(12, cls = "tamper"), incident(10)))
        engine.refresh()
        assertEquals(emptyList<Long>(), postedIds())
        assertTrue("the banner still shows", shellBanner(engine.state.value, false).show)
        sink.enabled = true
        engine.refresh()
        assertEquals("still open, so posted now", listOf(10L, 12L), postedIds())
    }

    @Test fun malformedPushesAreIgnored() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        engine.refresh()
        push("""{"banner":{"show":false}}""")
        push("[]")
        engine.onEvent(null)
        assertEquals(IncidentTier.HOUSEHOLD, engine.state.value.tier)
        assertEquals(listOf(12L), engine.state.value.open.map { it.id })
    }

    // ---- what a phone refused the list sees -------------------------------------

    @Test fun aPhoneRefusedTheListShowsTheSummaryAndPostsNothing() = runBlocking {
        a.answer(IncidentsApi.INCIDENTS_PATH, refusal, code = 401)
        a.answer(IncidentsApi.SUMMARY_PATH, """{"attention":true,"tone":"warn","text":"Something needs an admin's attention."}""")
        assertFalse(engine.refresh())
        val s = engine.state.value
        assertEquals(IncidentTier.OPEN, s.tier)
        assertTrue(s.loaded)
        assertEquals(emptyList<ContainerIncident>(), s.incidents)
        val banner = shellBanner(s, shared = false)
        assertTrue(banner.show)
        assertEquals(BannerTone.WARN, banner.tone)
        assertEquals(ADMIN_ATTENTION_TEXT, banner.text)
        assertEquals("no notification without the list", emptyList<Long>(), postedIds())
        assertEquals(listOf(IncidentsApi.INCIDENTS_PATH, IncidentsApi.SUMMARY_PATH), a.paths.toList())
        assertFalse("the guard's 401 does not raise the pairing screen", api.pairingRequired.value)
    }

    @Test fun theSummarys200DoesNotClearThePairingScreen() = runBlocking {
        api.notePossiblePairingRefusal(401, refusal)
        assertTrue(api.pairingRequired.value)
        a.answer(IncidentsApi.INCIDENTS_PATH, refusal, code = 401)
        a.answer(IncidentsApi.SUMMARY_PATH, """{"attention":false,"tone":null,"text":""}""")
        engine.refresh()
        assertTrue(api.pairingRequired.value)
        assertEquals(IncidentTier.OPEN, engine.state.value.tier)
        assertFalse(shellBanner(engine.state.value, false).show)
    }

    @Test fun anOlderServerShowsNothing() = runBlocking {
        engine.refresh()
        val s = engine.state.value
        assertEquals(IncidentTier.NONE, s.tier)
        assertTrue("answered: not a spinner forever", s.loaded)
        assertFalse(shellBanner(s, false).show)
    }

    @Test fun aPairedPhoneKeepsItsListThroughAFailedReadButNotThroughARefusal() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        engine.refresh()
        a.answer(IncidentsApi.INCIDENTS_PATH, """{"detail":"boom"}""", code = 500)
        a.answer(IncidentsApi.SUMMARY_PATH, """{"attention":true,"tone":"err","text":"x"}""")
        engine.refresh()
        assertEquals(IncidentTier.HOUSEHOLD, engine.state.value.tier)
        assertEquals(listOf(12L), engine.state.value.open.map { it.id })
        // The token went (unpaired, revoked): the list it had is no longer its to show.
        a.answer(IncidentsApi.INCIDENTS_PATH, refusal, code = 403)
        engine.refresh()
        assertEquals(IncidentTier.OPEN, engine.state.value.tier)
        assertEquals(1, sink.posts.size)
    }

    @Test fun noAnswerAtAllKeepsWhatWasShown() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        engine.refresh()
        // The connection drops before any answer, on both reads.
        val gone = MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)
        a.answers[IncidentsApi.INCIDENTS_PATH] = gone
        a.answers[IncidentsApi.SUMMARY_PATH] = gone
        assertFalse(engine.refresh())
        assertEquals(IncidentTier.HOUSEHOLD, engine.state.value.tier)
        assertTrue(shellBanner(engine.state.value, false).show)
    }

    // ---- the background sync's tick -----------------------------------------------

    @Test fun theTickAsksNothingWithoutAServerNotificationsOrWiFi() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        url = ""
        assertEquals(IncidentTick.NO_SERVER, engine.backgroundTick(onLan = true))
        url = a.url
        sink.enabled = false
        assertEquals(IncidentTick.NOTIFICATIONS_OFF, engine.backgroundTick(onLan = true))
        sink.enabled = true
        assertEquals(IncidentTick.OFF_LAN, engine.backgroundTick(onLan = false))
        assertEquals("not one request", emptyList<String>(), a.paths.toList())
        assertEquals(emptyList<Long>(), postedIds())
    }

    @Test fun theTickReadsPostsAndThenRestsWhileFresh() = runBlocking {
        a.list(payload(incident(12, cls = "tamper")))
        assertEquals(IncidentTick.CHECKED, engine.backgroundTick(onLan = true))
        assertEquals(listOf(12L), postedIds())
        now += INCIDENT_FRESH_MS - 1
        assertEquals(IncidentTick.FRESH, engine.backgroundTick(onLan = true))
        assertEquals(1, a.paths.size)
        // An incident opened while the app was off screen.
        a.list(payload(incident(13), incident(12, cls = "tamper")))
        now += 15 * 60_000L
        assertEquals(IncidentTick.CHECKED, engine.backgroundTick(onLan = true))
        assertEquals(listOf(12L, 13L), postedIds())
        // A push counts as fresh too.
        push(payload(incident(13), incident(12, cls = "tamper")))
        assertEquals(IncidentTick.FRESH, engine.backgroundTick(onLan = true))
    }

    @Test fun aRefusedTickIsUnreachableAndPostsNothing() = runBlocking {
        a.answer(IncidentsApi.INCIDENTS_PATH, refusal, code = 401)
        a.answer(IncidentsApi.SUMMARY_PATH, """{"attention":true,"tone":"err","text":"x"}""")
        assertEquals(IncidentTick.UNREACHABLE, engine.backgroundTick(onLan = true))
        assertEquals(emptyList<Long>(), postedIds())
    }

    data class Post(val serverKey: String, val content: IncidentContent)

    class FakeSink : IncidentSink {
        var enabled = true
        var sharedScreen = false
        val posts = CopyOnWriteArrayList<Post>()
        override fun canPost() = enabled
        override fun shared() = sharedScreen
        override fun post(serverKey: String, content: IncidentContent) {
            posts += Post(serverKey, content)
        }
    }
}
