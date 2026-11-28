package com.domovoi.app.away

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.Base64
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class AwaySessionTest {
    private val phone = Curve25519.newPrivate()

    /** A transport to a [FakeHouse]: counts opens and stops. */
    class Loop(private val house: FakeHouse) : AwayTransport {
        val opens = AtomicInteger()
        val stops = AtomicInteger()
        override suspend fun open(e: AwayEnrolment): AwayStream { opens.incrementAndGet(); return house.stream() }
        override fun stop() { stops.incrementAndGet() }
    }

    private fun enrolment(house: FakeHouse) = AwayEnrolment(
        torPrivate = Base64.getEncoder().encodeToString(Curve25519.newPrivate()),
        noisePrivate = Base64.getEncoder().encodeToString(phone),
        onion = "a".repeat(56) + ".onion", port = 80,
        houseNoisePub = AwayKeys.b64url(house.housePublic), proto = 1,
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Test fun nothingStartsUnlessTheChatScreenHoldsIt() = runBlocking {
        FakeHouse { it.next() }.use { house ->
            val t = Loop(house)
            val s = AwaySession(t, { enrolment(house) }, "1.5.0", scope)
            try {
                s.connection(); fail("connected with nobody holding it")
            } catch (e: AwayDropped) {
            }
            assertEquals(0, t.opens.get())
        }
    }

    @Test fun itStopsShortlyAfterTheLastRelease() = runBlocking {
        FakeHouse { c -> while (c.next(3_000) != null) Unit }.use { house ->
            val t = Loop(house)
            val s = AwaySession(t, { enrolment(house) }, "1.5.0", scope, graceMs = 200)
            s.acquire()
            s.connection()
            assertEquals(AwayLink.Connected, s.link.value)
            assertEquals(1, t.opens.get())
            s.connection()
            assertEquals("one connection, reused", 1, t.opens.get())
            s.release()
            s.acquire() // back within the grace: nothing stops
            delay(400)
            assertEquals(0, t.stops.get())
            s.release()
            delay(600)
            assertEquals(1, t.stops.get())
            assertEquals(AwayLink.Idle, s.link.value)
        }
    }

    @Test fun silenceIsUnreachableAndAsksForAStatusCheckAtHome() = runBlocking {
        val silences = LinkedBlockingQueue<String>()
        FakeHouse(allowed = setOf("nobody")) { }.use { house ->
            val s = AwaySession(Loop(house), { enrolment(house) }, "1.5.0", scope, onSilence = { silences.add(it ?: "silence") })
            s.acquire()
            try {
                s.connection(); fail("connected")
            } catch (e: AwayDropped) {
            }
            assertEquals(AwayLink.Failed(AwayText.UNREACHABLE), s.link.value)
            assertEquals("silence", silences.poll(2, TimeUnit.SECONDS))
        }
    }

    @Test fun aRevokedByeSaysSoAndAsksForAStatusCheck() = runBlocking {
        val silences = LinkedBlockingQueue<String>()
        FakeHouse { c ->
            c.next()
            c.send(AwayWire.HOUSE_BYE, 0, """{"code":"revoked"}""")
            Thread.sleep(300)
        }.use { house ->
            val s = AwaySession(Loop(house), { enrolment(house) }, "1.5.0", scope, onSilence = { silences.add(it ?: "silence") })
            s.acquire()
            val updates = s.client.send(1, "hi").toList()
            assertEquals(SendUpdate.Dropped("revoked"), updates.single())
            assertEquals("revoked", silences.poll(3, TimeUnit.SECONDS))
            delay(100)
            assertEquals(AwayLink.Failed(AwayText.NOT_SET_UP), s.link.value)
        }
    }

    @Test fun noEnrolmentIsNotSetUp() = runBlocking {
        FakeHouse { }.use { house ->
            val s = AwaySession(Loop(house), { null }, "1.5.0", scope)
            s.acquire()
            try {
                s.connection(); fail("connected without keys")
            } catch (e: AwayDropped) {
            }
            assertEquals(AwayLink.Failed(AwayText.NOT_SET_UP), s.link.value)
        }
    }

    @Test fun aDropMidReplyReconnectsAndTheStreamPicksItUp() = runBlocking {
        val second = AtomicInteger()
        FakeHouse { c ->
            if (second.getAndIncrement() == 0) {
                val m = c.next()!!
                c.send(AwayWire.RESP, m.req, """{"chat_id":7,"message_id":70,"user_message":{}}""")
                c.send(AwayWire.EVENT, m.req, """{"type":"delta","text":"Half"}""")
                c.close() // the network drops
            } else {
                val m = c.next()!!
                assertTrue(m.text.contains("chat.stream"))
                c.send(AwayWire.RESP, m.req, """{"streaming":true,"message_id":70}""")
                c.send(AwayWire.EVENT, m.req, """{"type":"delta","text":"Half a reply"}""")
                c.send(AwayWire.EVENT, m.req, """{"type":"done","message":{"id":71,"content":"Half a reply"}}""")
                c.next()
            }
        }.use { house ->
            val t = Loop(house)
            val s = AwaySession(t, { enrolment(house) }, "1.5.0", scope)
            s.acquire()
            val first = s.client.send(null, "hi").toList()
            assertTrue(first.last() is SendUpdate.Dropped)
            val again = s.client.stream(7).toList()
            assertTrue(again.first() is SendUpdate.Stored)
            assertEquals("Half a reply", (again[1] as SendUpdate.Delta).text)
            assertTrue(again.last() is SendUpdate.Done)
            assertEquals(2, t.opens.get())
        }
    }

    @Test fun aStreamWithNothingRunningSaysSo() = runBlocking {
        FakeHouse { c ->
            val m = c.next()!!
            c.send(AwayWire.RESP, m.req, """{"streaming":false}""")
            c.next()
        }.use { house ->
            val s = AwaySession(Loop(house), { enrolment(house) }, "1.5.0", scope)
            s.acquire()
            assertEquals(listOf(SendUpdate.NotRunning), s.client.stream(3).toList())
        }
    }

    @Test fun aSendThatMeetsSilenceSaysDroppedAndNeverThrows() = runBlocking {
        FakeHouse(allowed = setOf("nobody")) { }.use { house ->
            val s = AwaySession(Loop(house), { enrolment(house) }, "1.5.0", scope)
            s.acquire()
            assertEquals(listOf(SendUpdate.Dropped(null)), s.client.send(1, "hi").toList())
            assertEquals(listOf(SendUpdate.Dropped(null)), s.client.stream(1).toList())
            assertEquals(AwayLink.Failed(AwayText.UNREACHABLE), s.link.value)
        }
    }
}
