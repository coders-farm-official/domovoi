package com.domovoi.app.away

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class AwayConnectionTest {
    private val phone = Curve25519.newPrivate()

    private suspend fun connect(house: FakeHouse, pingMs: Long = 45_000) =
        AwayConnection.open(house.stream(), phone, house.housePublic, "1.5.0", timeoutMs = 5_000, pingEveryMs = pingMs)

    @Test fun theHandshakeSendsTheHelloAndReadsTheHouses() = runBlocking {
        val seen = LinkedBlockingQueue<ByteArray>()
        FakeHouse { c -> seen.add(c.phoneKey); seen.add(c.helloPayload); c.next() }.use { house ->
            val conn = connect(house)
            assertEquals(1_048_576L, conn.maxPicture)
            assertArrayEquals(Curve25519.publicOf(phone), seen.poll(5, TimeUnit.SECONDS))
            assertArrayEquals(AwayWire.phoneHello("1.5.0"), seen.poll(5, TimeUnit.SECONDS))
            conn.close()
        }
    }

    @Test fun aHouseThatClosesWithoutAWordIsSilence() = runBlocking {
        FakeHouse(allowed = setOf("nobody")) { }.use { house ->
            try {
                connect(house); fail("connected to a house that refused")
            } catch (e: AwaySilence) {
            }
        }
    }

    @Test fun aWrongHouseKeyIsSilenceToo() = runBlocking {
        FakeHouse { }.use { house ->
            try {
                AwayConnection.open(house.stream(), phone, Curve25519.publicOf(Curve25519.newPrivate()), "1.5.0", timeoutMs = 5_000)
                fail("connected with the wrong house key")
            } catch (e: AwaySilence) {
            }
        }
    }

    @Test fun anotherProtoIsRefused() = runBlocking {
        FakeHouse(hello = byteArrayOf(0, 2, 0, 0x10, 0, 0)) { it.next() }.use { house ->
            try {
                connect(house); fail("spoke proto 2")
            } catch (e: AwaySilence) {
            }
        }
    }

    @Test fun aSplitRespIsJoinedAndUnknownKeysIgnored() = runBlocking {
        val big = "x".repeat(100_000)
        FakeHouse { c ->
            val m = c.next()!!
            assertEquals(AwayWire.REQ, m.kind)
            assertTrue(m.text.contains("chat.list"))
            val body = """{"threads":[{"id":3,"title":"$big","new_field":1}],"next":null,"extra":true}""".toByteArray()
            val room = AwayWire.MAX_BODY
            val pieces = (body.indices step room).map { body.copyOfRange(it, minOf(it + room, body.size)) }
            pieces.forEachIndexed { i, p -> c.send(AwayWire.RESP, m.req, p, more = i < pieces.size - 1) }
            c.next()
        }.use { house ->
            val client = AwayClient(connect(house).let { conn -> { conn } })
            val page = client.list()
            assertTrue(page.toString().contains(big))
        }
    }

    @Test fun aSendStreamsDeltasThenDone() = runBlocking {
        FakeHouse { c ->
            val m = c.next()!!
            c.send(AwayWire.RESP, m.req, """{"chat_id":12,"message_id":345,"user_message":{"id":345,"role":"user","content":"hi"}}""")
            c.send(AwayWire.EVENT, m.req, """{"type":"delta","text":"Milk, "}""")
            c.send(AwayWire.EVENT, m.req, """{"type":"delta","text":"eggs"}""")
            c.send(AwayWire.EVENT, m.req, """{"type":"done","message":{"id":346,"role":"assistant","content":"Milk, eggs"}}""")
            c.next()
        }.use { house ->
            val conn = connect(house)
            val updates = withTimeout(10_000) { AwayClient { conn }.send(null, "hi").toList() }
            assertTrue(updates[0] is SendUpdate.Stored)
            assertEquals(12L, (updates[0] as SendUpdate.Stored).chatId)
            assertEquals("Milk, eggs", updates.filterIsInstance<SendUpdate.Delta>().joinToString("") { it.text })
            val done = updates.last() as SendUpdate.Done
            assertEquals("Milk, eggs", done.message!!["content"]!!.jsonPrimitive.content)
        }
    }

    @Test fun aTooLargeDoneSaysSoForAHistoryRead() = runBlocking {
        FakeHouse { c ->
            val m = c.next()!!
            c.send(AwayWire.RESP, m.req, """{"chat_id":1,"message_id":2,"user_message":{}}""")
            c.send(AwayWire.EVENT, m.req, """{"type":"done","message":{"id":3,"content":null,"too_large":true}}""")
            c.next()
        }.use { house ->
            val conn = connect(house)
            val done = AwayClient { conn }.send(1, "long one").toList().last() as SendUpdate.Done
            assertTrue(done.tooLarge)
        }
    }

    @Test fun aRefusedPicturesPiecesStop() = runBlocking {
        val blobs = LinkedBlockingQueue<Int>()
        FakeHouse { c ->
            val m = c.next()!!
            assertTrue(m.text.contains("\"picture\""))
            // Refuse at once, then count what still comes for it.
            c.send(AwayWire.ERR, m.req, """{"code":"unsupported_picture","detail":"no"}""")
            var n = 0
            while (true) {
                val b = c.next(1_500) ?: break
                if (b.kind == AwayWire.BLOB) n++ else break
            }
            blobs.add(n)
        }.use { house ->
            val conn = connect(house)
            // 16 pieces' worth: the phone must stop well before the end.
            val picture = ByteArray(1_000_000) { 1 }
            val updates = AwayClient { conn }.send(4, "", picture).toList()
            val refused = updates.single() as SendUpdate.Refused
            assertEquals("unsupported_picture", refused.code)
            assertEquals(AwayText.PICTURE, AwayText.forErr(refused.code))
            val sent = blobs.poll(10, TimeUnit.SECONDS)!!
            assertTrue("sent $sent pieces after the ERR", sent < 16)
        }
    }

    @Test fun aWholePictureArrivesInOrder() = runBlocking {
        val got = LinkedBlockingQueue<ByteArray>()
        FakeHouse { c ->
            val m = c.next()!!
            val size = Regex("\"size\":(\\d+)").find(m.text)!!.groupValues[1].toInt()
            val out = java.io.ByteArrayOutputStream()
            while (out.size() < size) {
                val b = c.next()!!
                assertEquals(AwayWire.BLOB, b.kind)
                assertEquals(m.req, b.req)
                val off = java.nio.ByteBuffer.wrap(b.body, 0, 4).int
                assertEquals(out.size(), off)
                out.write(b.body, 4, b.body.size - 4)
            }
            got.add(out.toByteArray())
            c.send(AwayWire.RESP, m.req, """{"chat_id":1,"message_id":2,"user_message":{}}""")
            c.send(AwayWire.EVENT, m.req, """{"type":"done","message":{"id":3}}""")
            c.next()
        }.use { house ->
            val conn = connect(house)
            val picture = ByteArray(300_000) { (it * 7).toByte() }
            AwayClient { conn }.send(1, "", picture).toList()
            assertArrayEquals(picture, got.poll(10, TimeUnit.SECONDS))
        }
    }

    @Test fun aByeEndsEveryOpenRequest() = runBlocking {
        FakeHouse { c ->
            c.next()
            c.send(AwayWire.HOUSE_BYE, 0, """{"code":"revoked"}""")
            Thread.sleep(300)
        }.use { house ->
            val conn = connect(house)
            val updates = AwayClient { conn }.send(1, "hi").toList()
            assertEquals(SendUpdate.Dropped("revoked"), updates.single())
            assertEquals("revoked", withTimeout(5_000) { conn.closed.first { it != null } }!!.code)
            assertEquals(AwayText.NOT_SET_UP, AwayText.forBye("revoked"))
        }
    }

    @Test fun anErrEndsOnlyItsRequest() = runBlocking {
        FakeHouse { c ->
            val m = c.next()!!
            c.send(AwayWire.ERR, m.req, """{"code":"rate","detail":"wait","retry_after":30}""")
            val m2 = c.next()!!
            c.send(AwayWire.RESP, m2.req, """{"cancelled":true}""")
            c.next()
        }.use { house ->
            val conn = connect(house)
            val client = AwayClient { conn }
            try {
                client.history(9); fail("not refused")
            } catch (e: AwayRefused) {
                assertEquals("rate", e.code)
                assertEquals(30L, e.retryAfter)
                assertEquals(AwayText.BUSY, e.line)
            }
            assertTrue(client.cancel(9))
        }
    }

    @Test fun atMostFourRequestsAreOpenAtOnce() = runBlocking {
        val maxOpen = LinkedBlockingQueue<Int>()
        FakeHouse { c ->
            val open = mutableListOf<Long>()
            repeat(4) { open += c.next()!!.req }
            // A fifth must not arrive while four are open.
            val early = c.next(800)
            maxOpen.add(if (early == null) 4 else 5)
            for (r in open) c.send(AwayWire.RESP, r, """{"threads":[],"next":null}""")
            c.next()?.let { c.send(AwayWire.RESP, it.req, """{"threads":[],"next":null}""") }
            c.next()
        }.use { house ->
            val conn = connect(house)
            val client = AwayClient { conn }
            coroutineScope { (1..5).map { async(Dispatchers.IO) { client.list() } }.awaitAll() }
            assertEquals(4, maxOpen.poll(5, TimeUnit.SECONDS))
        }
    }

    @Test fun aPingGoesWhenNothingElseDid() = runBlocking {
        val pings = LinkedBlockingQueue<Int>()
        FakeHouse { c ->
            val m = c.recv(5_000)
            pings.add(m?.kind ?: -1)
            if (m?.kind == AwayWire.PING) c.send(AwayWire.PONG, 0)
            c.next(2_000)
        }.use { house ->
            val conn = connect(house, pingMs = 300)
            assertEquals(AwayWire.PING, pings.poll(8, TimeUnit.SECONDS))
            assertTrue(conn.isOpen)
            conn.close()
        }
    }

    @Test fun closingSaysBye() = runBlocking {
        val kinds = LinkedBlockingQueue<Int>()
        FakeHouse { c -> kinds.add(c.next()?.kind ?: -1) }.use { house ->
            connect(house).close()
            assertEquals(AwayWire.BYE, kinds.poll(5, TimeUnit.SECONDS))
        }
    }

    @Test fun aSecondSendWhileOneIsRunningIsRefusedHereAsBusy() = runBlocking {
        val seen = LinkedBlockingQueue<Int>()
        FakeHouse { c ->
            val m = c.next()!!
            c.send(AwayWire.RESP, m.req, """{"chat_id":1,"message_id":2,"user_message":{}}""")
            // Only one request reaches the house while the reply runs.
            seen.add(if (c.next(1_000) == null) 1 else 2)
            c.send(AwayWire.EVENT, m.req, """{"type":"done","message":{"id":3}}""")
            c.next()
        }.use { house ->
            val conn = connect(house)
            val client = AwayClient { conn }
            coroutineScope {
                val first = async(Dispatchers.IO) { client.send(1, "one").toList() }
                kotlinx.coroutines.delay(300)
                val second = client.send(1, "two").toList()
                assertEquals("busy", (second.single() as SendUpdate.Refused).code)
                assertTrue(first.await().last() is SendUpdate.Done)
            }
            assertEquals(1, seen.poll(5, TimeUnit.SECONDS))
        }
    }
}
