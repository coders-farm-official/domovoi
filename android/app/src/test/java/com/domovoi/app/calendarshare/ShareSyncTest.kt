package com.domovoi.app.calendarshare

import com.domovoi.app.net.ApiException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.IOException

/** Turning sharing on and off, picking, and what the house is sent or told
 *  to delete — the pending delete above all. */
class ShareSyncTest {
    @get:Rule val tmp = TemporaryFolder()

    private val server = "http://192.168.1.20:6369"
    private var now = 1_800_000_000_000L
    private val work = PhoneCalendar(12, "Work", "alex@example.com", 0xFF33B679.toInt())
    private val home = PhoneCalendar(3, "Home")
    private var onPhone: List<PhoneCalendar>? = listOf(work, home)
    private val puts = mutableListOf<JsonObject>()
    private var deletes = 0
    private var failWith: Exception? = null

    private fun store() = ShareStore(tmp.root.resolve("calendar-share.json"))

    private fun sync(store: ShareStore) = ShareSync(
        store = store,
        calendars = { onPhone },
        instances = { _, ids -> if (onPhone == null) null else ids.map { InstanceRow(it * 100, it, now + 1000, now + 2000, false, "e$it", null) } },
        put = { _, body -> failWith?.let { throw it }; puts += body },
        delete = { _ -> failWith?.let { throw it }; deletes++ },
        phoneName = { "Pixel 8" },
        now = { now },
    )

    private fun sentIds(body: JsonObject) = body["calendars"]!!.jsonArray.map { it.jsonObject["calendar_id"]!!.jsonPrimitive.content }

    @Test fun offByDefaultAndNothingIsSentUntilACalendarIsTicked() = runBlocking {
        val s = store()
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get(server)))
        s.update(server) { ShareRules.turnOn(it) }
        // On, but nothing ticked: turning it on sends nothing by itself.
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get(server)))
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertTrue(puts.isEmpty())
        s.update(server) { ShareRules.toggle(it, 12, true) }
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(listOf("12"), sentIds(puts.single()))
        assertEquals(now, s.get(server)!!.lastSentAtMs)
        assertNull(s.get(server)!!.lastError)
    }

    @Test fun unpickingLeavesTheCalendarOutOfTheNextSend() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12, 3)) }
        sync(s).run(server)
        assertEquals(listOf("3", "12"), sentIds(puts.last()))
        s.update(server) { ShareRules.toggle(it, 12, false) }
        sync(s).run(server)
        assertEquals(listOf("3"), sentIds(puts.last()))
        // Unticking the last one still sends (an empty list empties the
        // house's copy), because something was sent before.
        s.update(server) { ShareRules.toggle(it, 3, false) }
        assertEquals(ShareAction.Put, ShareRules.action(s.get(server)))
        sync(s).run(server)
        assertEquals(emptyList<String>(), sentIds(puts.last()))
    }

    @Test fun turningOffDeletesTheHousesCopy() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        sync(s).run(server)
        s.update(server) { ShareRules.turnOff(it) }
        assertEquals(ShareAction.Delete, ShareRules.action(s.get(server)))
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(1, deletes)
        assertFalse(s.get(server)!!.pendingDelete)
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get(server)))
        assertFalse(ShareRules.needsJob(s.all()))
    }

    @Test fun aDeleteThatCantReachTheServerIsRememberedAndTriedAgain() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.turnOff(ShareRules.pick(ShareRules.turnOn(it), listOf(12))) }
        failWith = IOException("offline")
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
        assertTrue("still pending", s.get(server)!!.pendingDelete)
        assertTrue(ShareRules.needsJob(s.all()))
        // It survives the app being closed (read back from the file).
        val reopened = store()
        assertTrue(reopened.get(server)!!.pendingDelete)
        assertEquals("the house's copy isn't deleted yet: couldn't reach your Domovoi; trying again later",
            com.domovoi.app.ui.screens.settings.shareStatusLine(reopened.get(server)))
        failWith = null
        assertEquals(ShareSync.Outcome.Done, sync(reopened).run(server))
        assertEquals(1, deletes)
        assertFalse(reopened.get(server)!!.pendingDelete)
    }

    @Test fun turningBackOnBeforeTheDeleteWentReplacesInstead() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.turnOff(ShareRules.pick(ShareRules.turnOn(it), listOf(12))) }
        s.update(server) { ShareRules.turnOn(it) }
        assertFalse(s.get(server)!!.pendingDelete)
        sync(s).run(server)
        assertEquals(0, deletes)
        assertEquals(listOf("12"), sentIds(puts.single()))
    }

    @Test fun anOlderServerWithoutTheRouteCountsAsDeleted() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.turnOff(it) }
        failWith = ApiException(404, "404 Not Found")
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertFalse(s.get(server)!!.pendingDelete)
    }

    @Test fun aRateLimitIsWaitedOut() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = ApiException(429, "429 Too Many Requests", retryAfterSec = 45)
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
        assertEquals(45_000L, ShareRules.waitMs(s.get(server), now))
        failWith = null
        now += 10_000
        // Too soon: not even tried.
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
        assertTrue(puts.isEmpty())
        now += 40_000
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(1, puts.size)
        assertNull(s.get(server)!!.notBeforeMs)
    }

    @Test fun aRefusalIsSaidAndNotHammered() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = ApiException(413, "413 Payload Too Large")
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals("too many events to send; pick fewer calendars", s.get(server)!!.lastError)
        failWith = ApiException(503, "503")
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
    }

    @Test fun aWithdrawnPermissionIsSaidAndTheCopyIsLeftAlone() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        onPhone = null
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertTrue(puts.isEmpty())
        assertEquals(ShareSync.PERMISSION_GONE, s.get(server)!!.lastError)
    }

    @Test fun eachServerHasItsOwnEntry() {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        assertNull(s.get("http://192.168.1.30:6369"))
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get("http://192.168.1.30:6369")))
    }

    @Test fun theStatusLineSaysWhereThingsStand() {
        assertNull(com.domovoi.app.ui.screens.settings.shareStatusLine(null))
        assertNull(com.domovoi.app.ui.screens.settings.shareStatusLine(ShareEntry()))
        assertEquals("tick the calendars to share",
            com.domovoi.app.ui.screens.settings.shareStatusLine(ShareEntry(enabled = true)))
        assertEquals("sending soon…",
            com.domovoi.app.ui.screens.settings.shareStatusLine(ShareEntry(enabled = true, calendarIds = listOf(1))))
        assertEquals("deleting the house's copy…",
            com.domovoi.app.ui.screens.settings.shareStatusLine(ShareEntry(pendingDelete = true)))
        val sent = com.domovoi.app.ui.screens.settings.shareStatusLine(
            ShareEntry(enabled = true, calendarIds = listOf(1), lastSentAtMs = System.currentTimeMillis(), lastDropped = 3))!!
        assertTrue(sent, sent.startsWith("last sent just now"))
        assertTrue(sent, sent.endsWith("3 events left out (too many to send)"))
    }

    @Test fun theDisclosureSaysWhatWhereAndHowToStop() {
        val d = com.domovoi.app.ui.screens.settings.calendarShareDisclosure("Home server")
        listOf("title, time and", "place", "your Domovoi server (Home server)", "nothing goes to Coders Farm",
            "Descriptions and guests stay on this phone", "deleted from the server when you turn this off").forEach {
            assertTrue(it, it in d)
        }
    }
}
