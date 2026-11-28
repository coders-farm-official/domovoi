package com.domovoi.app.away

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AwayEnrollTest {
    private val onion = "a".repeat(56) + ".onion"
    private val houseKey = AwayKeys.b64url(Curve25519.publicOf(Curve25519.newPrivate()))

    class Book : AwayKeyBook {
        val map = mutableMapOf<String, AwayEnrolment>()
        var writable = true
        override fun awayEnrolmentFor(server: String) = map[server]
        override fun rememberAwayEnrolment(server: String, e: AwayEnrolment?): Boolean {
            if (!writable) return false
            if (e == null) map.remove(server) else map[server] = e
            return true
        }
    }

    class Http(val answer: (String, String, JsonObject?) -> Pair<Int, String>) : AwayHttp {
        val calls = mutableListOf<Pair<String, JsonObject?>>()
        private fun go(method: String, path: String, body: JsonObject?): JsonElement {
            calls += "$method $path" to body
            val (status, text) = answer(method, path, body)
            if (status !in 200..299) throw AwayHttpError(status, text)
            return Json.parseToJsonElement(text)
        }
        override suspend fun post(path: String, body: JsonObject) = go("POST", path, body)
        override suspend fun get(path: String) = go("GET", path, null)
        override suspend fun delete(path: String) = go("DELETE", path, null)
    }

    private fun ok() = 200 to """{"onion":"$onion","port":80,"house_noise_pub":"$houseKey","proto":1,"state":"starting","later":1}"""

    @Test fun base32IsRfc4648UpperCaseWithoutPadding() {
        assertEquals("MZXW6YTBOI", AwayKeys.base32("foobar".toByteArray()))
        assertEquals("MZXW6", AwayKeys.base32("foo".toByteArray()))
        // A 32-byte key: the 52 characters the house expects.
        assertEquals(52, AwayKeys.base32(ByteArray(32) { 9 }).length)
    }

    @Test fun base64urlRoundTripsAndRefusesOtherShapes() {
        val raw = ByteArray(32) { (it * 13).toByte() }
        val text = AwayKeys.b64url(raw)
        assertEquals(43, text.length)
        assertArrayEquals(raw, AwayKeys.unb64url(text, 32))
        assertNull(AwayKeys.unb64url(text + "A", 32))
        assertNull(AwayKeys.unb64url(text.dropLast(1), 32))
        assertNull(AwayKeys.unb64url("+".repeat(43), 32))
    }

    @Test fun torsAuthPrivateLineNamesTheOnionAndTheKey() {
        val priv = ByteArray(32) { 1 }
        assertEquals("${"b".repeat(56)}:descriptor:x25519:${AwayKeys.base32(priv)}", AwayKeys.authPrivateLine("b".repeat(56), priv))
    }

    @Test fun theTwoKeyPairsAreFreshAndDifferent() {
        val a = AwayKeyPairs.generate()
        val b = AwayKeyPairs.generate()
        assertFalse(a.torPublic.contentEquals(a.noisePublic))
        assertFalse(a.noisePublic.contentEquals(b.noisePublic))
        assertTrue(a.torAuthPub.matches(Regex("^[A-Z2-7]{52}$")))
        assertTrue(a.noisePub.matches(Regex("^[A-Za-z0-9_-]{43}$")))
        assertEquals("AwayKeyPairs(…)", a.toString())
    }

    @Test fun enrollingSendsThePublicHalvesAndKeepsThePrivateOnes() = runBlocking {
        val http = Http { _, _, _ -> ok() }
        val book = Book()
        val keys = AwayKeyPairs.generate()
        val r = AwayEnroll.enroll(http, book, "http://192.168.1.20:6369", keys) as EnrollResult.Ok
        val (call, body) = http.calls.single()
        assertEquals("POST /api/away/enroll", call)
        assertEquals(setOf("tor_auth_pub", "noise_pub"), body!!.keys)
        assertEquals(keys.torAuthPub, body["tor_auth_pub"]!!.jsonPrimitive.content)
        assertEquals(keys.noisePub, body["noise_pub"]!!.jsonPrimitive.content)
        val kept = book.map["http://192.168.1.20:6369"]!!
        assertEquals(r.enrolment, kept)
        assertArrayEquals(keys.torPrivate, kept.torPrivateBytes())
        assertArrayEquals(keys.noisePrivate, kept.noisePrivateBytes())
        assertEquals(onion, kept.onion)
        assertEquals("a".repeat(56), kept.onionHost)
        assertEquals(80, kept.port)
        assertEquals(1, kept.proto)
        assertNotNull(kept.housePublicBytes())
        assertFalse("never prints a key", kept.toString().contains(kept.noisePrivate))
        // Round-trips through the vault's record.
        assertEquals(kept, AwayKeys.decode(AwayKeys.encode(kept)))
    }

    @Test fun everyRefusalHasItsLineAndKeepsNothing() = runBlocking {
        val cases = listOf(
            409 to "away_off" to AwayText.AWAY_OFF,
            409 to "internet_off" to AwayText.INTERNET_OFF,
            409 to "guest" to AwayEnroll.refusalLine(409, "guest"),
            403 to "lan_only" to AwayEnroll.refusalLine(403, "lan_only"),
            400 to "bad_key" to AwayText.WRONG,
            401 to "device_key_required" to AwayEnroll.refusalLine(401, "device_key_required"),
        )
        for ((sc, line) in cases) {
            val (status, code) = sc
            val book = Book()
            val r = AwayEnroll.enroll(Http { _, _, _ -> status to """{"detail":"x","code":"$code"}""" }, book, "s")
            r as EnrollResult.Refused
            assertEquals(code, r.code)
            assertEquals(status, r.status)
            assertEquals(line, r.line)
            assertTrue(book.map.isEmpty())
        }
        val notSetUp = AwayEnroll.enroll(Http { _, _, _ -> 501 to """{"detail":"set up first"}""" }, Book(), "s") as EnrollResult.Refused
        assertTrue(notSetUp.line.contains("isn't set up"))
        val tier = AwayEnroll.enroll(Http { _, _, _ -> 401 to """{"detail":"X-Device-Token or admin session required"}""" }, Book(), "s") as EnrollResult.Refused
        assertTrue(tier.line.contains("Pair it again"))
        // The lines a person sees are distinct and plain.
        assertTrue(AwayEnroll.refusalLine(409, "guest").contains("household members"))
        assertTrue(AwayEnroll.refusalLine(403, "lan_only").contains("home network"))
    }

    @Test fun anAnswerThatDoesntFitKeepsNothing() = runBlocking {
        for (bad in listOf(
            """{"onion":"short.onion","port":80,"house_noise_pub":"$houseKey","proto":1}""",
            """{"onion":"$onion","port":0,"house_noise_pub":"$houseKey","proto":1}""",
            """{"onion":"$onion","port":80,"house_noise_pub":"nope","proto":1}""",
            """{"onion":"$onion","port":80,"house_noise_pub":"$houseKey","proto":2}""",
        )) {
            val book = Book()
            val r = AwayEnroll.enroll(Http { _, _, _ -> 200 to bad }, book, "s")
            assertTrue(bad, r is EnrollResult.Refused)
            assertTrue(book.map.isEmpty())
        }
        val unwritable = Book().apply { writable = false }
        assertTrue(AwayEnroll.enroll(Http { _, _, _ -> ok() }, unwritable, "s") is EnrollResult.Refused)
    }

    @Test fun turningItOffTellsTheHouseAndWipesWhateverItAnswers() = runBlocking {
        val book = Book()
        AwayEnroll.enroll(Http { _, _, _ -> ok() }, book, "s")
        val http = Http { _, _, _ -> 200 to """{"ok":true}""" }
        assertTrue(AwayEnroll.unenroll(http, book, "s"))
        assertEquals("DELETE /api/away/enroll", http.calls.single().first)
        assertTrue(book.map.isEmpty())
        AwayEnroll.enroll(Http { _, _, _ -> ok() }, book, "s")
        assertFalse(AwayEnroll.unenroll(Http { _, _, _ -> 503 to "{}" }, book, "s"))
        assertTrue("wiped anyway", book.map.isEmpty())
    }

    @Test fun statusSaysWhy() = runBlocking {
        fun st(json: String) = runBlocking { AwayEnroll.status(Http { _, _, _ -> 200 to json })!! }
        assertNull(st("""{"house":"on","enrolled":true,"state":"ready"}""").line)
        assertEquals(AwayText.AWAY_OFF, st("""{"house":"away_off","enrolled":true,"state":null}""").line)
        assertEquals(AwayText.INTERNET_OFF, st("""{"house":"internet_off","enrolled":true,"state":null}""").line)
        assertEquals(AwayText.SET_UP_AGAIN, st("""{"house":"on","enrolled":false,"state":null}""").line)
        assertNull(AwayEnroll.status(Http { _, _, _ -> 500 to "{}" }))
    }

    @Test fun theLinesAreTheAgreedWords() {
        // The same strings as the iPhone app (core-0074 brief).
        assertEquals("Connecting to your house…", AwayText.CONNECTING)
        assertEquals("Can't reach your house.", AwayText.UNREACHABLE)
        assertEquals("Chat from away is turned off on your house.", AwayText.AWAY_OFF)
        assertEquals("Your house's internet setting doesn't allow chat from away.", AwayText.INTERNET_OFF)
        assertEquals("Your house's AI is turned off.", AwayText.AI_OFF)
        assertEquals("Away chat isn't set up for this phone any more.", AwayText.NOT_SET_UP)
        assertEquals("Away chat needs setting up again at home.", AwayText.SET_UP_AGAIN)
        assertEquals("That picture couldn't be sent.", AwayText.PICTURE)
        assertEquals("Your house is busy. Try again shortly.", AwayText.BUSY)
        assertEquals("Something went wrong.", AwayText.WRONG)
        assertEquals("Chat with your house when you're away? You can turn this off any time.", AwayText.OFFER)
        assertEquals(AwayText.AI_OFF, AwayText.forErr("ai_off"))
        assertEquals(AwayText.BUSY, AwayText.forErr("unavailable"))
        assertEquals(AwayText.WRONG, AwayText.forErr("bad_request"))
        assertEquals(AwayText.AWAY_OFF, AwayText.forBye("away_off"))
        assertEquals(AwayText.UNREACHABLE, AwayText.forBye(null))
    }
}
