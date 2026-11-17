package com.domovoi.app.friendhouses

import com.domovoi.app.net.ApiClient
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The background sync's Friend Houses tick against a fake dashboard: the
 * notification per alert level (inbox: none; sender: the name only; read:
 * the name and the text, cut and cleaned), when it asks the server at all,
 * and that no name or message ever reaches a log line.
 */
class FriendHouseAlertsTest {
    private lateinit var server: MockWebServer
    private lateinit var url: String
    private lateinit var alerts: FriendHouseAlerts

    private val answers = HashMap<String, String>()
    private val asked = CopyOnWriteArrayList<String>()
    private val logs = CopyOnWriteArrayList<String>()
    private val statuses = CopyOnWriteArrayList<FhAvailability>()

    private val smiths = "ab".repeat(32)
    private val patels = "cd".repeat(32)
    private val hostileName = "The Smiths‮\u0007"
    private val hostileBody = "http://x <b>y</b> **z**\u0000\u001B[31m\nsecond line"

    private class Sink : FhSink {
        var allowed = true
        val posts = CopyOnWriteArrayList<FhNote>()
        val withdrawn = CopyOnWriteArrayList<String>()
        override fun canPost() = allowed
        override fun post(serverKey: String, note: FhNote) { posts += note }
        override fun withdraw(serverKey: String, house: String) { withdrawn += house }
    }

    private class Store : FhSeenStore {
        val map = HashMap<String, FhSeen>()
        override suspend fun read(serverKey: String) = map[serverKey]
        override suspend fun write(serverKey: String, seen: FhSeen) { map[serverKey] = seen }
    }

    private val sink = Sink()
    private val store = Store()

    @Before fun up() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val key = "${request.method} ${request.path}"
                asked += key
                val body = answers[key] ?: return MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                return MockResponse().setBody(body)
            }
        }
        server.start()
        url = server.url("/").toString().trimEnd('/')
        alerts = FriendHouseAlerts(
            api = FriendHousesApi(ApiClient(baseUrlProvider = { url }, deviceTokenProvider = { "household-token-1234" })),
            store = store,
            sink = sink,
            serverUrl = { url },
            serverKey = { "k" },
            onStatus = { _, a -> statuses += a },
            log = { logs += it },
        )
    }

    @After fun down() = server.shutdown()

    private fun status(unread: Int, s: String = "on") { answers["GET /api/friend-houses"] = """{"status":"$s","unread":$unread}""" }

    private fun inbox(vararg c: Triple<String, Int, Long>, friendIds: Map<String, String?> = mapOf(smiths to "f1", patels to "f2")) {
        answers["GET /api/friend-houses/conversations"] = """{"conversations":[${c.joinToString(",") { (h, unread, last) ->
            val fid = friendIds[h]?.let { "\"$it\"" } ?: "null"
            """{"house":"$h","name":${q(if (h == smiths) hostileName else "The Patels")},"friend_id":$fid,"unread":$unread,"last_id":$last,"not_delivered":0}"""
        }}],"unread":${c.sumOf { it.second }}}"""
    }

    private fun friends(smithsAlert: String, patelsAlert: String = "inbox") {
        answers["GET /api/friend-houses/friends"] = """{"friends":[
            {"id":"f1","name":${q(hostileName)},"house":"$smiths","alert":"$smithsAlert","verified":true},
            {"id":"f2","name":"The Patels","house":"$patels","alert":"$patelsAlert","verified":false}]}"""
    }

    private fun messages(house: String, vararg m: Triple<Long, String, String>) {
        answers["GET /api/friend-houses/conversations/$house?limit=50"] = """{"messages":[${m.joinToString(",") { (id, state, body) ->
            """{"id":$id,"direction":"${if (state == "sent") "out" else "in"}","body":${q(body)},"state":"$state","sent_at":1,"created_at":"2026-11-17T18:25:33+00:00"}"""
        }}]}"""
    }

    private fun q(s: String) = Json.encodeToString(JsonPrimitive.serializer(), JsonPrimitive(s))

    /** A phone that has looked once already, with nothing unread then. */
    private fun looked(unread: Int = 0, ids: Map<String, Long> = emptyMap()) { store.map["k"] = FhSeen(unread, ids) }

    // ---- the decision per alert level --------------------------------------

    @Test fun inboxMakesNoNotification() = runBlocking {
        looked()
        status(1); inbox(Triple(smiths, 1, 10)); friends("inbox")
        messages(smiths, Triple(10, "unread", "Dinner at six?"))
        assertEquals(FhTick.QUIET, alerts.backgroundTick(onLan = true))
        assertTrue(sink.posts.isEmpty())
        // The friend's level said "inbox": the messages weren't even fetched.
        assertTrue(asked.none { it.contains("?limit=") })
        assertEquals(FhSeen(1, mapOf(smiths to 10L)), store.map["k"])
    }

    @Test fun senderSaysWhoItIsFromWithNoText() = runBlocking {
        looked()
        status(2); inbox(Triple(smiths, 2, 11)); friends("sender")
        messages(smiths, Triple(10, "unread", hostileBody), Triple(11, "unread", "second"))
        assertEquals(FhTick.NOTIFIED, alerts.backgroundTick(onLan = true))
        val n = sink.posts.single()
        assertEquals("A message from The Smiths", n.title)
        assertNull(n.text)
        assertEquals(2, n.count)
        assertEquals(smiths, n.house)
        assertEquals(11L, n.newestId)
    }

    @Test fun readShowsTheNameAndTheTextCutAndCleaned() = runBlocking {
        looked()
        val long = "a".repeat(150) + "\u0007‮" + "b".repeat(150)
        status(1); inbox(Triple(smiths, 1, 12)); friends("read")
        messages(smiths, Triple(9, "read", "old"), Triple(12, "unread", long))
        alerts.backgroundTick(onLan = true)
        val n = sink.posts.single()
        assertEquals("The Smiths", n.title)
        assertEquals("a".repeat(150) + "b".repeat(50) + "…", n.text)
    }

    @Test fun anAlertLevelThisBuildDoesntKnowIsNeverLouder() {
        assertNull(FhNotify.noteFor("shout", smiths, "x", "y", 1, 1))
        assertNull(FhNotify.noteFor(null, smiths, "x", "y", 1, 1))
        assertNull(FhNotify.noteFor("inbox", smiths, "x", "y", 1, 1))
        assertNull(FhNotify.noteFor("read", smiths, "x", "y", 0, 1))
    }

    @Test fun notificationTextKeepsLineBreaksAndDropsControlCharacters() {
        val n = FhNotify.noteFor("read", smiths, hostileName, hostileBody, 1, 1)!!
        assertEquals("The Smiths", n.title)
        assertEquals("http://x <b>y</b> **z**[31m\nsecond line", n.text)
        assertTrue(n.text!!.none { it.code < 0x20 && it != '\n' })
        // Bidi controls and C1 controls go too; tabs become spaces.
        assertEquals("a b c", FhNotify.cleanText("a⁦\tb\u0085 c‏"))
        // A name is one line.
        assertEquals("A message from Two lines", FhNotify.noteFor("sender", smiths, "Two\nlines", null, 1, 1)!!.title)
        assertEquals("A message from a friend house", FhNotify.noteFor("sender", smiths, "\u0007", null, 1, 1)!!.title)
    }

    @Test fun theCutNeverSplitsACharacter() {
        val emoji = "😀"
        val cut = FhNotify.cut(emoji.repeat(250))
        assertEquals(emoji.repeat(200) + "…", cut)
        assertEquals("short", FhNotify.cut("short"))
        assertEquals("x".repeat(200), FhNotify.cut("x".repeat(200)))
    }

    // ---- when it asks, and what -------------------------------------------

    @Test fun theFirstLookIsABaselineNotANotification() = runBlocking {
        status(3); inbox(Triple(smiths, 3, 40)); friends("read")
        messages(smiths, Triple(40, "unread", "old news"))
        assertEquals(FhTick.BASELINE, alerts.backgroundTick(onLan = true))
        assertTrue(sink.posts.isEmpty())
        assertEquals(FhSeen(3, mapOf(smiths to 40L)), store.map["k"])
        // Then one new message: notified.
        status(4); inbox(Triple(smiths, 4, 41))
        messages(smiths, Triple(40, "unread", "old news"), Triple(41, "unread", "new"))
        assertEquals(FhTick.NOTIFIED, alerts.backgroundTick(onLan = true))
        assertEquals("new", sink.posts.single().text)
        assertEquals(1, sink.posts.single().count)
    }

    @Test fun anUnchangedUnreadCountAsksNothingMore() = runBlocking {
        looked(unread = 2, ids = mapOf(smiths to 10L))
        status(2)
        assertEquals(FhTick.QUIET, alerts.backgroundTick(onLan = true))
        assertEquals(listOf("GET /api/friend-houses"), asked)
    }

    @Test fun everythingReadTakesTheNotificationsBack() = runBlocking {
        looked(unread = 2, ids = mapOf(smiths to 10L, patels to 4L))
        status(0)
        assertEquals(FhTick.QUIET, alerts.backgroundTick(onLan = true))
        assertEquals(setOf(smiths, patels), sink.withdrawn.toSet())
        assertEquals(listOf("GET /api/friend-houses"), asked)
        assertEquals(0, store.map["k"]!!.unread)
    }

    @Test fun eachConversationFollowsItsOwnFriendsLevel() = runBlocking {
        looked(ids = mapOf(smiths to 5L, patels to 5L))
        status(2); inbox(Triple(smiths, 1, 6), Triple(patels, 1, 7)); friends(smithsAlert = "sender", patelsAlert = "inbox")
        messages(smiths, Triple(6, "unread", "x"))
        messages(patels, Triple(7, "unread", "y"))
        alerts.backgroundTick(onLan = true)
        assertEquals(listOf(smiths), sink.posts.map { it.house })
        assertTrue(asked.none { it.contains(patels) })
    }

    @Test fun aFormerFriendsMessagesNotifyNothing() = runBlocking {
        looked()
        status(1); inbox(Triple(smiths, 1, 6), friendIds = mapOf(smiths to null)); friends("read")
        messages(smiths, Triple(6, "unread", "x"))
        assertEquals(FhTick.QUIET, alerts.backgroundTick(onLan = true))
        assertTrue(sink.posts.isEmpty())
    }

    @Test fun offOrNotOfferedAsksNothingMore() = runBlocking {
        looked()
        status(5, s = "off")
        assertEquals(FhTick.HIDDEN, alerts.backgroundTick(onLan = true))
        assertEquals(listOf("GET /api/friend-houses"), asked)
        assertEquals(FhAvailability.Hidden, statuses.single())
        answers.remove("GET /api/friend-houses") // a server before Friend Houses: 404
        assertEquals(FhTick.HIDDEN, alerts.backgroundTick(onLan = true))
        assertTrue(sink.posts.isEmpty())
    }

    @Test fun offWifiNotificationsOffOrNoServerAsksNothing() = runBlocking {
        status(1)
        assertEquals(FhTick.OFF_LAN, alerts.backgroundTick(onLan = false))
        sink.allowed = false
        assertEquals(FhTick.NOTIFICATIONS_OFF, alerts.backgroundTick(onLan = true))
        assertTrue(asked.isEmpty())
        val none = FriendHouseAlerts(
            FriendHousesApi(ApiClient(baseUrlProvider = { "" })), store, sink, serverUrl = { "" }, serverKey = { "k" },
        )
        assertEquals(FhTick.NO_SERVER, none.backgroundTick(onLan = true))
    }

    @Test fun theStatusAnswerFeedsTheInAppGate() = runBlocking {
        looked(unread = 1)
        status(1)
        alerts.backgroundTick(onLan = true)
        assertEquals(1, (statuses.single() as FhAvailability.Shown).status.unread)
    }

    // ---- nothing logs a name or a message -----------------------------------

    @Test fun noLogLineCarriesANameOrAMessage() = runBlocking {
        looked()
        status(1); inbox(Triple(smiths, 1, 12)); friends("read")
        messages(smiths, Triple(12, "unread", hostileBody))
        alerts.backgroundTick(onLan = true)
        // And a failure part-way: the friends list is broken JSON.
        status(2); inbox(Triple(smiths, 2, 13))
        answers["GET /api/friend-houses/friends"] = """{"friends":[{"name":${q(hostileBody)},"alert":"""
        messages(smiths, Triple(13, "unread", hostileBody))
        assertEquals(FhTick.UNREACHABLE, alerts.backgroundTick(onLan = true))
        assertTrue(logs.isNotEmpty())
        logs.forEach { line ->
            listOf("Smiths", "http://x", "<b>", "**z**", "second line", "Patels").forEach { bit ->
                assertTrue("\"$line\" carries \"$bit\"", !line.contains(bit))
            }
        }
    }

    /** The Android side logs nothing it was handed from another house: every
     *  Log call in the friendhouses package names fixed words, a count, or an
     *  exception's class, never its message. */
    @Test fun theAndroidSideLogsNoText() {
        val repo = File(System.getProperty("domovoi.repo.dir") ?: error("domovoi.repo.dir is not set"))
        val dir = File(repo, "android/app/src/main/java/com/domovoi/app/friendhouses")
        val calls = dir.listFiles()!!.filter { it.extension == "kt" }.flatMap { f ->
            f.readLines().filter { "Log." in it || "log(" in it || "log =" in it }
        }
        assertTrue(calls.isNotEmpty())
        calls.forEach { line ->
            listOf(".message", ".body", ".name", ".title", ".text", "lastError", "detail").forEach { bad ->
                assertTrue("\"${line.trim()}\" may log $bad", !line.contains(bad))
            }
        }
    }
}
