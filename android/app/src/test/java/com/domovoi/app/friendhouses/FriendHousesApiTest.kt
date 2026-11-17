package com.domovoi.app.friendhouses

import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DEVICE_TOKEN_HEADER
import com.domovoi.app.ui.screens.friendhouses.FhThread
import com.domovoi.app.ui.screens.friendhouses.FhThreadLoader
import com.domovoi.app.ui.screens.friendhouses.FhWords
import com.domovoi.app.ui.screens.friendhouses.fhMerge
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
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
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The phone's Friend Houses client against a fake dashboard (the payloads
 * of the mobile hand-off, F5): the availability gate, every route's path,
 * header and absence of a query token, paging, mark-read, send and its
 * errors, the admin-only words, and the house-key check.
 */
class FriendHousesApiTest {
    private lateinit var server: MockWebServer
    private lateinit var url: String
    private lateinit var client: ApiClient
    private lateinit var api: FriendHousesApi

    private val answers = HashMap<String, MockResponse>()
    private val seen = CopyOnWriteArrayList<RecordedRequest>()

    private val house = "ab".repeat(32)
    private val token = "household-token-1234"

    private fun answer(method: String, path: String, body: String, code: Int = 200) {
        answers["$method $path"] = MockResponse().setResponseCode(code).setBody(body)
            .setHeader("Content-Type", "application/json")
    }

    @Before fun up() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                seen += request
                return answers["${request.method} ${request.path}"]
                    ?: MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
            }
        }
        server.start()
        url = server.url("/").toString().trimEnd('/')
        client = ApiClient(baseUrlProvider = { url }, deviceTokenProvider = { token })
        api = FriendHousesApi(client)
    }

    @After fun down() = server.shutdown()

    private fun status(s: String, unread: Int = 2) =
        """{"status":"$s","detail":${if (s == "stopped") "\"The internet answer is \\\"never\\\".\"" else "null"},
           "fingerprint":null,"onion_ready":true,"pairing_ready":false,"listener_running":true,"firewall":"in_place",
           "house_name":"A Domovoi house","invite_days":7,"unread":$unread,"limits":{"min_days":1,"max_days":30,"max_open_files":10}}"""

    private fun msg(id: Long, direction: String = "in", state: String = "read", body: String = "hi $id") =
        """{"id":$id,"direction":"$direction","body":${kotlinx.serialization.json.JsonPrimitive(body)},
           "state":"$state","sent_at":1795466733,"created_at":"2026-11-17T18:25:33+00:00","delivered_at":null,"last_error":null}"""

    // ---- the availability gate -------------------------------------------------

    @Test fun theGateShowsOnlyWhenTheServerSaysItIsOnStartingStoppedOrError() = runBlocking {
        for (s in listOf("on", "starting", "stopped", "error")) {
            answer("GET", "/api/friend-houses", status(s))
            val a = api.status()
            assertTrue(s, a is FhAvailability.Shown)
            assertEquals(s, (a as FhAvailability.Shown).status.status)
            assertEquals(2, a.status.unread)
        }
        for (s in listOf("off", "unavailable", "something-new")) {
            // A house that was on keeps its unread count when turned off: still hidden.
            answer("GET", "/api/friend-houses", status(s, unread = 5))
            assertEquals(s, FhAvailability.Hidden, api.status())
        }
        answer("GET", "/api/friend-houses", "not json")
        assertEquals(FhAvailability.Hidden, api.status())
        answer("GET", "/api/friend-houses", """{"unread":3}""")
        assertEquals(FhAvailability.Hidden, api.status())
    }

    @Test fun refusedMissingOrBeforeSetupHidesItAndNeverAsksToPair() = runBlocking {
        // A server before F5 refuses the household token here (admin tier);
        // an even older one has no route; before setup it's 501.
        val bodies = mapOf(
            401 to """{"detail":"admin session required"}""",
            403 to """{"detail":"$DEVICE_TOKEN_HEADER required — the dashboard cookie does not authorize device-tier actions"}""",
            404 to """{"detail":"Not Found"}""",
            501 to """{"detail":"set up an admin first"}""",
        )
        for ((code, body) in bodies) {
            answer("GET", "/api/friend-houses", body, code)
            assertEquals("$code", FhAvailability.Hidden, api.status())
            assertFalse("a $code on the status call never raises pairing", client.pairingRequired.value)
        }
    }

    @Test fun noAnswerOrAServerErrorKeepsTheLastVerdict() = runBlocking {
        answer("GET", "/api/friend-houses", """{"detail":"core unreachable"}""", 502)
        assertEquals(FhAvailability.Unknown, api.status())
        answer("GET", "/api/friend-houses", """{"detail":"wait"}""", 429)
        assertEquals(FhAvailability.Unknown, api.status())
        val shown = FhAvailability.Shown(FhStatus("on", unread = 1))
        assertEquals(shown, FhGate.settle(shown, FhAvailability.Unknown))
        assertEquals(FhAvailability.Hidden, FhGate.settle(null, FhAvailability.Unknown))
        assertEquals(FhAvailability.Hidden, FhGate.settle(shown, FhAvailability.Hidden))
        // Nothing answers at all.
        val nowhere = FriendHousesApi(ApiClient(baseUrlProvider = { "http://127.0.0.1:9" }, deviceTokenProvider = { token }))
        assertEquals(FhAvailability.Unknown, nowhere.status())
    }

    @Test fun theGateIsPerServer() {
        val state = FhGateState("http://192.168.1.20:6369", FhAvailability.Shown(FhStatus("on", unread = 4)))
        assertEquals(4, state.shownFor("http://192.168.1.20:6369/")?.unread)
        assertNull(state.shownFor("http://192.168.1.21:6369"))
        assertNull(FhGateState().shownFor("http://192.168.1.20:6369"))
        assertNull(FhGateState("http://192.168.1.20:6369", FhAvailability.Hidden).shownFor("http://192.168.1.20:6369"))
    }

    @Test fun theStatusLineIsTheServersSentence() {
        assertEquals("The internet answer is \"never\".", fhStatusLine(FhStatus("stopped", detail = "The internet answer is \"never\".")))
        assertEquals("Friend Houses isn't running right now.", fhStatusLine(FhStatus("error")))
        assertEquals("Friend Houses is starting.", fhStatusLine(FhStatus("starting")))
        assertNull(fhStatusLine(FhStatus("on")))
    }

    // ---- the routes: path, header, no query token, no cookie -------------------

    @Test fun everyRouteCarriesTheHeaderAndNothingElse() = runBlocking {
        answer("GET", "/api/friend-houses", status("on"))
        answer("GET", "/api/friend-houses/friends", """{"friends":[]}""")
        answer("GET", "/api/friend-houses/conversations", """{"conversations":[],"unread":0}""")
        answer("GET", "/api/friend-houses/conversations/$house?limit=50", """{"messages":[]}""")
        answer("GET", "/api/friend-houses/conversations/$house?limit=50&before=7", """{"messages":[]}""")
        answer("POST", "/api/friend-houses/conversations/$house/read", """{"marked":3}""")
        answer("POST", "/api/friend-houses/messages", """{"id":9,"state":"queued","created_at":"2026-11-17T18:25:33+00:00"}""")
        answer("GET", "/api/friend-houses/shared-lists", """{"shared_lists":[]}""")

        api.status()
        api.friends()
        api.inbox()
        api.conversation(house)
        api.conversation(house, before = 7)
        assertEquals(3, api.markRead(house))
        api.send("fa1b2c3d4e5f6", "See you at six")
        api.sharedLists()

        assertEquals(
            listOf(
                "GET /api/friend-houses",
                "GET /api/friend-houses/friends",
                "GET /api/friend-houses/conversations",
                "GET /api/friend-houses/conversations/$house?limit=50",
                "GET /api/friend-houses/conversations/$house?limit=50&before=7",
                "POST /api/friend-houses/conversations/$house/read",
                "POST /api/friend-houses/messages",
                "GET /api/friend-houses/shared-lists",
            ),
            seen.map { "${it.method} ${it.path}" },
        )
        seen.forEach { r ->
            assertEquals(r.path, token, r.getHeader(DEVICE_TOKEN_HEADER))
            assertNull(r.path, r.getHeader("Cookie"))
            assertNull(r.path, r.getHeader("Authorization"))
            assertFalse(r.path, r.path!!.contains("token", ignoreCase = true))
            assertFalse(r.path, r.path!!.contains("See you"))
            // Only the house key and numbers ever go in a URL.
            val rest = r.path!!.removePrefix("/api/friend-houses").replace(house, "")
            assertTrue(r.path, Regex("[a-z/\\-?=&0-9]*").matches(rest))
        }
    }

    @Test fun pagingAsksForTheNewestPageThenEarlierOnesByBefore() = runBlocking {
        val newest = (51L..100L).joinToString(",") { msg(it) }
        val earlier = (11L..50L).joinToString(",") { msg(it) }
        answer("GET", "/api/friend-houses/conversations/$house?limit=50", """{"messages":[$newest]}""")
        answer("GET", "/api/friend-houses/conversations/$house?limit=50&before=51", """{"messages":[$earlier]}""")
        val loader = FhThreadLoader(api, house)
        val (first, unread) = loader.latest(FhThread(), first = true)
        assertFalse(unread)
        assertTrue("a full page: there may be earlier ones", first.more)
        assertEquals(100L, first.messages.last().id)
        val more = loader.earlier(first)
        assertEquals((11L..100L).toList(), more.messages.map { it.id })
        assertFalse("a short page: nothing earlier", more.more)
        assertEquals(100L, more.messages.last().id) // the newest stays at the bottom
        // A later poll that brings a new unread message keeps the earlier ones.
        answer("GET", "/api/friend-houses/conversations/$house?limit=50",
            """{"messages":[${(52L..100L).joinToString(",") { msg(it) }},${msg(101, state = "unread")}]}""")
        val (polled, unreadNow) = loader.latest(more, first = false)
        assertTrue(unreadNow)
        assertEquals(11L, polled.messages.first().id)
        assertEquals(101L, polled.messages.last().id)
        assertFalse(polled.more)
    }

    @Test fun limitsAreClampedAndBeforeMustBeAnId() {
        assertEquals("/api/friend-houses/conversations/$house?limit=200", FhPaths.conversation(house, limit = 5000))
        assertEquals("/api/friend-houses/conversations/$house?limit=1", FhPaths.conversation(house, limit = 0))
        assertEquals("/api/friend-houses/conversations/$house?limit=50", FhPaths.conversation(house, before = 0))
    }

    @Test fun mergeKeepsOneOfEachOldestFirst() {
        val a = listOf(FhMessage(id = 3), FhMessage(id = 1))
        val b = listOf(FhMessage(id = 2), FhMessage(id = 3, state = "read"))
        val m = fhMerge(a, b)
        assertEquals(listOf(1L, 2L, 3L), m.map { it.id })
        assertEquals("read", m.last().state)
    }

    @Test fun markReadPostsToTheConversation() = runBlocking {
        answer("POST", "/api/friend-houses/conversations/$house/read", """{"marked":2}""")
        assertEquals(2, FhThreadLoader(api, house).markRead())
        assertEquals("POST /api/friend-houses/conversations/$house/read", seen.single().let { "${it.method} ${it.path}" })
    }

    // ---- send ------------------------------------------------------------------

    @Test fun sendPutsTheTextInTheBodyOnly() = runBlocking {
        answer("POST", "/api/friend-houses/messages", """{"id":9,"state":"queued","created_at":"2026-11-17T18:25:33+00:00"}""")
        val text = "Dinner at six?\nBring the <b>pie</b>"
        val sent = api.send("fa1b2c3d4e5f6", text)
        assertEquals(9L, sent.id)
        assertEquals("queued", sent.state)
        val r = seen.single()
        val body = Json.parseToJsonElement(r.body.readUtf8()).jsonObject
        assertEquals(setOf("friend_id", "text"), body.keys)
        assertEquals("fa1b2c3d4e5f6", body["friend_id"]!!.jsonPrimitive.content)
        assertEquals(text, body["text"]!!.jsonPrimitive.content)
        assertEquals("/api/friend-houses/messages", r.path)
    }

    @Test fun sendErrorsShowTheServersDetailAsIs() = runBlocking {
        val said = mapOf(
            400 to "That house isn't a friend any more.",
            404 to "No such friend house.",
        )
        for ((code, detail) in said) {
            answer("POST", "/api/friend-houses/messages", """{"detail":"$detail"}""", code)
            try {
                api.send("fa1b2c3d4e5f6", "hello")
                fail("$code should throw")
            } catch (e: Exception) {
                assertEquals(detail, fhProblem("send", e))
            }
        }
        // A 403 from any call: the admin sentence.
        answer("POST", "/api/friend-houses/messages", """{"detail":"mutations require Authorization: Bearer"}""", 403)
        try {
            api.send("fa1b2c3d4e5f6", "hello")
            fail("403 should throw")
        } catch (e: Exception) {
            assertEquals(FH_ADMIN_ONLY, fhProblem("send", e))
        }
        answer("GET", "/api/friend-houses/friends", """{"detail":"admin only"}""", 403)
        try {
            api.friends()
            fail("403 should throw")
        } catch (e: Exception) {
            assertEquals(FH_ADMIN_ONLY, fhProblem("load friends", e))
        }
        // A detail that isn't a sentence (a validation list) is not echoed.
        answer("POST", "/api/friend-houses/messages", """{"detail":[{"msg":"x","input":"secret"}]}""", 400)
        try {
            api.send("fa1b2c3d4e5f6", "hello")
            fail("400 should throw")
        } catch (e: Exception) {
            assertEquals("send failed (400)", fhProblem("send", e))
        }
    }

    @Test fun whatMayBeSentIsOneToTwoThousandCharacters() {
        assertFalse(fhSendable(""))
        assertFalse(fhSendable("   \n "))
        assertTrue(fhSendable("a"))
        assertTrue(fhSendable("a".repeat(2000)))
        assertFalse(fhSendable("a".repeat(2001)))
        // Counted as the server counts: an emoji is one character, not two.
        val emoji = "😀"
        assertEquals(1, fhLength(emoji))
        assertTrue(fhSendable(emoji.repeat(2000)))
        assertFalse(fhSendable(emoji.repeat(2001)))
    }

    @Test fun aBadFriendIdIsNeverSent() = runBlocking {
        for (id in listOf("", "../x", "a b", "x".repeat(65))) {
            try {
                api.send(id, "hello")
                fail("$id should be refused")
            } catch (e: IllegalArgumentException) {
                assertEquals("not a friend id", e.message)
            }
        }
        assertTrue(seen.isEmpty())
    }

    // ---- the house key ---------------------------------------------------------

    @Test fun onlyASixtyFourHexHouseKeyGoesInAPath() = runBlocking {
        assertTrue(FhPaths.isHouseKey(house))
        val bad = listOf(
            "", "AB".repeat(32), "ab".repeat(31), "ab".repeat(33), "ab".repeat(31) + "zz",
            "ab".repeat(32) + "\n", "../" + "a".repeat(61), "ab".repeat(16) + "/" + "ab".repeat(15) + "a",
            "ab".repeat(31) + "a?", null,
        )
        for (b in bad) {
            assertFalse("$b", FhPaths.isHouseKey(b))
            if (b == null) continue
            for (call in listOf<suspend () -> Unit>({ api.conversation(b) }, { api.markRead(b) })) {
                try {
                    call()
                    fail("$b should be refused")
                } catch (e: IllegalArgumentException) {
                    // The refusal never repeats the value.
                    assertEquals("not a house key", e.message)
                }
            }
        }
        assertTrue("nothing was sent", seen.isEmpty())
        // The inbox drops a conversation whose key a URL can't carry.
        val inbox = FhInbox(listOf(FhConversation(house = house, lastId = 1), FhConversation(house = "nope", lastId = 9)))
        assertEquals(listOf(house), inbox.newestFirst().map { it.house })
    }

    @Test fun theInboxIsNewestFirst() {
        val other = "cd".repeat(32)
        val inbox = FhInbox(listOf(FhConversation(house = house, lastId = 5), FhConversation(house = other, lastId = 118)))
        assertEquals(listOf(other, house), inbox.newestFirst().map { it.house })
    }

    @Test fun thePayloadsDecodeAsTheHandOffWritesThem() = runBlocking {
        answer("GET", "/api/friend-houses/friends", """{"friends":[{"id":"fa1b2c3d4e5f6","name":"The Smiths","fingerprint":"SHA256:x",
            "house":"$house","alert":"sender","code":"4821","verified":false,"added_at":"2026-11-17T18:25:33+00:00"}]}""")
        answer("GET", "/api/friend-houses/conversations", """{"conversations":[{"house":"$house","name":"The Smiths",
            "friend_id":null,"unread":2,"last_id":118,"not_delivered":1}],"unread":2}""")
        answer("GET", "/api/friend-houses/shared-lists", """{"shared_lists":[{"id":"${"0f".repeat(16)}","state":"invited","origin":"them",
            "title":"Camping","list_id":null,"list_name":null,"friend":"The Smiths","friend_id":"fa1b2c3d4e5f6",
            "created_at":"2026-11-17T18:25:33+00:00","waiting_to_send":0}]}""")
        val f = api.friends().single()
        assertEquals("sender", f.alert)
        assertFalse(f.verified)
        val c = api.inbox().conversations.single()
        assertNull(c.friendId)
        assertEquals(118L, c.lastId)
        assertEquals(1, c.notDelivered)
        val s = api.sharedLists().single()
        assertEquals("invited", s.state)
        assertNull(s.listId)
    }

    // ---- the admin-only words --------------------------------------------------

    @Test fun adminOnlyThingsAreWordsThatSayTheDashboard() {
        listOf(FH_ADMIN_ONLY, FhWords.FRIENDS_ADMIN, FhWords.LISTS_ADMIN, FhWords.CONVERSATION_ADMIN).forEach {
            assertTrue(it, it.startsWith("Only an admin can "))
            assertTrue(it, it.contains("on the dashboard"))
        }
        assertEquals(
            "The Smiths shared a list: Camping. An admin can accept it in the dashboard.",
            FhWords.invited("The Smiths", "Camping"),
        )
        assertEquals("Serverless messaging and friend system between households", FH_DESCRIPTION)
        val words = listOf(FH_DESCRIPTION, FH_ADMIN_ONLY, FhWords.FRIENDS_ADMIN, FhWords.LISTS_ADMIN,
            FhWords.CONVERSATION_ADMIN, FhWords.PHONE_FOLLOWS, FhWords.NO_FRIENDS_SUB)
        assertTrue(words.none { it.contains("gam", ignoreCase = true) })
    }

    @Test fun alertLevelsAreWords() {
        assertEquals("New messages: only in the inbox", FhWords.alert("inbox"))
        assertEquals("New messages: the rooms say who it's from", FhWords.alert("sender"))
        assertEquals("New messages: read aloud in the rooms", FhWords.alert("read"))
        assertEquals("New messages: as set on the dashboard", FhWords.alert("loud"))
    }

    @Test fun anErrorBodyWithoutADetailFallsBack() {
        assertNull(detailOf(null))
        assertNull(detailOf("<html>"))
        assertNull(detailOf("""{"detail":""}"""))
        assertEquals("x", detailOf("""{"detail":"x"}"""))
        assertTrue(Json.parseToJsonElement("""{"a":1}""") is JsonObject)
    }
}
