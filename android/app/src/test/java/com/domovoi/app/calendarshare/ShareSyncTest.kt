package com.domovoi.app.calendarshare

import com.domovoi.app.net.ApiException
import com.domovoi.app.net.ServerChangedException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.long
import kotlinx.serialization.json.put
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
    private val other = "http://192.168.1.30:6369"
    private var now = 1_800_000_000_000L
    private val work = PhoneCalendar(12, "Work", "alex@example.com", 0xFF33B679.toInt())
    private val home = PhoneCalendar(3, "Home")
    private var onPhone: List<PhoneCalendar>? = listOf(work, home)
    private var rowsFor: (Set<Long>) -> List<InstanceRow> = { ids -> ids.map { InstanceRow(it * 100, it, now + 1000, now + 2000, false, "e$it", null) } }
    private val puts = mutableListOf<JsonObject>()
    private var deletes = 0
    private var calendarReads = 0
    private var instanceReads = 0
    private var failWith: Exception? = null
    private var duringPut: () -> Unit = {}
    /** What the house answers a PUT; the keys and generations sent. */
    private var answer: JsonElement? = null
    private val putKeys = mutableListOf<String?>()
    private val deleteKeys = mutableListOf<String?>()
    private val deleteGenerations = mutableListOf<Long>()
    private val keys = object : CalendarKeys {
        val saved = mutableMapOf<String, String>()
        override fun calendarKeyFor(server: String) = saved[server]
        override fun setCalendarKey(server: String, key: String?) {
            if (key == null) saved.remove(server) else saved[server] = key
        }
    }

    private fun store() = ShareStore(tmp.root.resolve("calendar-share.json"))

    private fun sync(store: ShareStore) = ShareSync(
        store = store,
        calendars = { calendarReads++; onPhone },
        instances = { _, ids -> instanceReads++; if (onPhone == null) null else rowsFor(ids) },
        put = { _, body, key -> failWith?.let { throw it }; duringPut(); puts += body; putKeys += key; answer },
        delete = { _, key, generation -> failWith?.let { throw it }; deletes++; deleteKeys += key; deleteGenerations += generation },
        phoneName = { "Pixel 8" },
        keys = keys,
        now = { now },
    )

    private fun sentIds(body: JsonObject) = body["calendars"]!!.jsonArray.map { it.jsonObject["calendar_id"]!!.jsonPrimitive.content }

    @Test fun offByDefaultAndNothingIsSentUntilACalendarIsTicked() = runBlocking {
        val s = store()
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get(server)))
        // Off: the phone's calendars are not even read.
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(0, calendarReads)
        assertEquals(0, instanceReads)
        s.update(server) { ShareRules.turnOn(it) }
        // On, but nothing ticked: turning it on sends (and reads) nothing by itself.
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get(server)))
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(0, calendarReads)
        assertEquals(0, instanceReads)
        assertTrue(puts.isEmpty())
        s.update(server) { ShareRules.toggle(it, 12, true, "Work") }
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
        assertFalse(ShareRules.needsJob(s.all(), server, paired = true))
    }

    /** Review HIGH 3: sharing turned off while the PUT was on its way. The
     *  pass reads the state again after the send and deletes at once, so
     *  the house never keeps the copy that PUT left. */
    @Test fun turningOffDuringAnUploadDeletesRightAfterIt() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        duringPut = { s.update(server) { ShareRules.turnOff(it) } }
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals("the PUT went", 1, puts.size)
        assertEquals("and the delete right after it", 1, deletes)
        assertFalse(s.get(server)!!.pendingDelete)
        assertNull(s.get(server)!!.lastSentAtMs)
    }

    @Test fun aDeleteThatCantReachTheServerIsRememberedAndTriedAgain() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.turnOff(ShareRules.pick(ShareRules.turnOn(it), listOf(12))) }
        failWith = IOException("offline")
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
        assertTrue("still pending", s.get(server)!!.pendingDelete)
        assertTrue(ShareRules.needsJob(s.all(), server, paired = true))
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

    @Test fun aRateLimitIsWaitedOutAndNotShownAsAFault() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = ApiException(429, "429 Too Many Requests", retryAfterSec = 45)
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
        assertEquals(45_000L, ShareRules.waitMs(s.get(server), now))
        assertFalse("a 429 is not in the warning colour", ShareRules.warns(s.get(server)))
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
        assertTrue(ShareRules.warns(s.get(server)))
        failWith = ApiException(503, "503")
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
    }

    /** Review MEDIUM 6: the permission taken back is as good as turned
     *  off: the house's copy is deleted, and the panel says why. (Was
     *  aWithdrawnPermissionIsSaidAndTheCopyIsLeftAlone.) */
    @Test fun aWithdrawnPermissionTurnsSharingOffAndDeletesTheCopy() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        sync(s).run(server)
        onPhone = null
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(1, puts.size)
        assertEquals(1, deletes)
        val e = s.get(server)!!
        assertFalse(e.enabled)
        assertFalse(e.pendingDelete)
        assertEquals(ShareRules.PERMISSION_LOST, e.notice)
        assertEquals(ShareRules.PERMISSION_LOST, com.domovoi.app.ui.screens.settings.shareStatusLine(e))
        assertTrue(ShareRules.warns(e))
        // Out of reach: the delete stays pending, and is said with the notice.
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = IOException("offline")
        assertEquals(ShareSync.Outcome.Retry, sync(s).run(server))
        assertTrue(s.get(server)!!.pendingDelete)
        assertTrue(com.domovoi.app.ui.screens.settings.shareStatusLine(s.get(server))!!
            .startsWith(ShareRules.PERMISSION_LOST + " · the house's copy isn't deleted yet"))
        // Turning it on again clears the notice.
        assertNull(ShareRules.turnOn(s.get(server)).notice)
    }

    /** A provider that fails is tried again; it is not a lost permission
     *  and it unticks nothing. */
    @Test fun aFailingProviderChangesNothing() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        val failing = ShareSync(s, { throw IOException("provider") }, { _, _ -> emptyList() },
            { _, b, _ -> puts += b; null }, { _, _, _ -> deletes++ }, { null }, keys, { now })
        assertEquals(ShareSync.Outcome.Retry, failing.run(server))
        assertTrue(s.get(server)!!.enabled)
        assertEquals(listOf(12L), s.get(server)!!.calendarIds)
        assertEquals(0, deletes)
    }

    /** Review MEDIUM 6: a ticked calendar gone from the phone (its account
     *  removed, its id changed, or hidden) is named and unticked; the send
     *  still goes so the house drops it, and the line does not read as if
     *  all were well. */
    @Test fun aTickedCalendarThatVanishedIsNamedAndUnticked() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.toggle(ShareRules.toggle(ShareRules.turnOn(it), 12, true, "Work"), 3, true, "Home") }
        sync(s).run(server)
        assertEquals(listOf("3", "12"), sentIds(puts.last()))
        onPhone = listOf(home)
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(listOf("3"), sentIds(puts.last()))
        val e = s.get(server)!!
        assertEquals(listOf(3L), e.calendarIds)
        assertEquals("Work is no longer on this phone, so it is no longer shared", e.notice)
        val line = com.domovoi.app.ui.screens.settings.shareStatusLine(e)!!
        assertTrue(line, line.startsWith("Work is no longer on this phone"))
        assertTrue(ShareRules.warns(e))
        // The last one gone too: still sent (empty) so the house drops it.
        onPhone = emptyList()
        sync(s).run(server)
        assertEquals(emptyList<String>(), sentIds(puts.last()))
        assertEquals("Home is no longer on this phone, so it is no longer shared", s.get(server)!!.notice)
        // Ticking again clears the notice.
        assertNull(ShareRules.toggle(s.get(server), 5, true, "New").notice)
    }

    /** Review MEDIUM 7: nothing changed, nothing sent; but once a day it
     *  goes anyway so the house's window keeps moving. */
    @Test fun anUnchangedSnapshotIsNotSentAgainUntilADayHasPassed() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        sync(s).run(server)
        assertEquals(1, puts.size)
        // Six hours on, the window has moved but nothing in it has: skipped.
        // (The rows are pinned to the first send's times here.)
        val fixed = now
        rowsFor = { ids -> ids.map { InstanceRow(it * 100, it, fixed + 1000, fixed + 2000, false, "e$it", null) } }
        now += 6 * 3_600_000L
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals("unchanged: not sent", 1, puts.size)
        // A change is sent.
        rowsFor = { ids -> ids.map { InstanceRow(it * 100, it, fixed + 1000, fixed + 2000, false, "renamed", null) } }
        sync(s).run(server)
        assertEquals(2, puts.size)
        // A day after the last send, the same again goes.
        now += ShareRules.RESEND_MS
        sync(s).run(server)
        assertEquals(3, puts.size)
        // Turned off and on: sent again even if unchanged (the house deleted it).
        s.update(server) { ShareRules.turnOff(it) }
        sync(s).run(server)
        s.update(server) { ShareRules.turnOn(it) }
        sync(s).run(server)
        assertEquals(4, puts.size)
    }

    /** Review HIGH 2: forgetting the server (or unpairing) deletes the
     *  house's copy first, then the entry; a delete that fails is said. */
    @Test fun withdrawDeletesTheCopyThenForgetsTheEntry() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        sync(s).run(server)
        assertTrue(sync(s).withdraw(server))
        assertEquals(1, deletes)
        assertNull(s.get(server))
        // Nothing ever sent there: no call, nothing to say.
        assertTrue(sync(s).withdraw(other))
        assertEquals(1, deletes)
        // Out of reach: the entry still goes (the server is being
        // forgotten), and false tells the forget flow to say so.
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = IOException("offline")
        assertFalse(sync(s).withdraw(server))
        assertNull(s.get(server))
        assertEquals("forgot 192.168.1.20:6369 — this phone's calendars stay on the house until removed there",
            com.domovoi.app.ui.shell.forgotServerToast("192.168.1.20:6369", calendarsGone = false))
        // A server without the route never had a copy.
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = ApiException(404, "404")
        assertTrue(sync(s).withdraw(server))
    }

    /** Review "test the worker's server capture and onlyTo": every call of
     *  a pass goes ONLY to the server the pass started for. When the app
     *  moves to another server mid-pass, the client refuses
     *  (ServerChangedException) and nothing reaches the other household. */
    @Test fun everyCallGoesOnlyToTheServerThePassWasFor() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        var active = server
        val sent = mutableListOf<Triple<String, String, String>>()
        val seenHeaders = mutableListOf<Map<String, String>>()
        val http = object : ShareHttp {
            // As ApiClient.raw: a call for another server is not sent; one
            // with no onlyTo goes to whichever server is active.
            override suspend fun put(path: String, body: JsonObject, headers: Map<String, String>, onlyTo: String?): JsonElement? {
                if (onlyTo != null && onlyTo != active) throw ServerChangedException()
                sent += Triple("PUT", path, active)
                seenHeaders += headers
                return buildJsonObject { put("calendars", 1); put("events", 1); put("calendar_key", if (sent.size == 1) "k-1" else null) }
            }
            override suspend fun delete(path: String, headers: Map<String, String>, onlyTo: String?) {
                if (onlyTo != null && onlyTo != active) throw ServerChangedException()
                sent += Triple("DELETE", path, active)
                seenHeaders += headers
            }
        }
        val calls = ShareCalls("android-1a/2b", http)
        fun pass() = ShareSync(s, { onPhone }, { _, ids -> rowsFor(ids) }, calls::put, calls::delete, { null }, keys, { now })
        assertEquals(ShareSync.Outcome.Done, pass().run(server))
        assertEquals(listOf(Triple("PUT", "/api/calendar/phones/android-1a%2F2b", server)), sent)
        // No key before the first snapshot; the one it answered is kept.
        assertEquals(emptyMap<String, String>(), seenHeaders[0])
        assertEquals("k-1", keys.calendarKeyFor(server))
        // The app moves to another server while the pass reads the phone.
        val moving = ShareSync(s, { active = other; onPhone }, { _, ids -> listOf(InstanceRow(9, 12, now, null, false, "new", null)) + rowsFor(ids) },
            calls::put, calls::delete, { null }, keys, { now })
        assertEquals(ShareSync.Outcome.Retry, moving.run(server))
        assertEquals("nothing went to the other server", 1, sent.size)
        assertEquals(1, sent.count { it.third == server })
        active = server
        s.update(server) { ShareRules.turnOff(it) }
        pass().run(server)
        // The DELETE carries the key and the generation it is at.
        assertEquals(Triple("DELETE", "/api/calendar/phones/android-1a%2F2b?generation=$now", server), sent.last())
        assertEquals(mapOf("X-Domovoi-Calendar-Key" to "k-1"), seenHeaders.last())
    }

    @Test fun eachServerHasItsOwnEntry() {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        assertNull(s.get(other))
        assertEquals(ShareAction.Nothing, ShareRules.action(s.get(other)))
    }

    /** Review LOW: the jobs run only for the ACTIVE, paired server. */
    @Test fun theJobRunsOnlyForTheActivePairedServer() {
        val all = mapOf(server to ShareEntry(enabled = true, calendarIds = listOf(1)), other to ShareEntry(pendingDelete = true))
        assertTrue(ShareRules.needsJob(all, server, paired = true))
        assertTrue(ShareRules.needsJob(all, other, paired = true))
        assertFalse("not paired", ShareRules.needsJob(all, server, paired = false))
        assertFalse("no server", ShareRules.needsJob(all, "", paired = true))
        assertFalse("a forgotten or never-shared server", ShareRules.needsJob(all, "http://10.0.0.9:6369", paired = true))
        assertFalse(ShareRules.needsJob(mapOf(server to ShareEntry()), server, paired = true))
    }

    /** Review LOW: an unreadable file must not lose a pending delete. */
    @Test fun aCorruptFileKeepsEveryServerItNamesAsToDelete() {
        val f = tmp.root.resolve("calendar-share.json")
        f.writeText("""{"$server":{"enabled":true,"calendarIds":[12],"lastSentAtMs":17""")
        val s = ShareStore(f)
        val e = s.get(server)!!
        assertFalse(e.enabled)
        assertTrue(e.pendingDelete)
        assertEquals(ShareAction.Delete, ShareRules.action(e))
        assertTrue("moved aside", tmp.root.resolve("calendar-share.json.corrupt").exists())
        // And saved, so it is still there after a restart.
        assertTrue(ShareStore(f).get(server)!!.pendingDelete)
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

    @Test fun theDisclosureSaysWhatWhereWhoAndHowToStop() {
        val d = com.domovoi.app.ui.screens.settings.calendarShareDisclosure("Home server")
        listOf("title, time and", "place", "your Domovoi server (Home server)", "nothing goes to Coders Farm",
            "Everyone in your household can see them, and Domovoi can read them out in any room.",
            "Private events go as \"busy\", with no title or place",
            "Descriptions and guests stay on this phone", "deleted from the server when you turn this off").forEach {
            assertTrue(it, it in d)
        }
        val p = com.domovoi.app.ui.screens.settings.PRIVACY_CALENDAR_POINT
        assertTrue(p, "Everyone in your household can see them, and Domovoi can read them out in any room." in p)
        // Said while the switch is ON: no "turn this on again".
        assertFalse("turn this on" in com.domovoi.app.ui.screens.settings.CALENDAR_PERMISSION_MISSING)
    }

    // ─── Server V057: the calendar key and the generation ─────────────

    /** The key is saved from the first answer, sent on every later PUT and
     *  DELETE, kept when an answer has none, and replaced by a new one. */
    @Test fun theCalendarKeyIsSavedSentAndReplaced() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        answer = buildJsonObject { put("calendars", 1); put("events", 1); put("calendar_key", "first-key") }
        sync(s).run(server)
        assertEquals(listOf<String?>(null), putKeys)
        assertEquals("first-key", keys.calendarKeyFor(server))
        // Later answers say null: the saved key stays and is sent.
        answer = buildJsonObject { put("calendars", 1); put("events", 1); put("calendar_key", JsonNull) }
        rowsFor = { ids -> ids.map { InstanceRow(it * 100, it, now + 5000, now + 6000, false, "changed", null) } }
        sync(s).run(server)
        assertEquals("first-key", putKeys.last())
        assertEquals("first-key", keys.calendarKeyFor(server))
        // After an admin removal or a 7-day expiry the house hands a new one.
        answer = buildJsonObject { put("calendars", 1); put("events", 1); put("calendar_key", "second-key") }
        rowsFor = { ids -> ids.map { InstanceRow(it * 100, it, now + 7000, now + 8000, false, "again", null) } }
        sync(s).run(server)
        assertEquals("second-key", keys.calendarKeyFor(server))
        // Turned off: the DELETE carries it, and the key is kept (the house
        // still knows this phone by it).
        s.update(server) { ShareRules.turnOff(it) }
        sync(s).run(server)
        assertEquals(listOf<String?>("second-key"), deleteKeys)
        assertEquals("second-key", keys.calendarKeyFor(server))
        // Forgetting the server forgets the key too.
        assertTrue(sync(s).withdraw(server))
        assertNull(keys.calendarKeyFor(server))
        assertEquals(null, ShareSync.calendarKey(buildJsonObject { put("calendar_key", "  ") }))
        assertEquals(null, ShareSync.calendarKey(null))
    }

    /** A 403 on a PUT: the house no longer takes this phone's key. Said in
     *  the panel, and not retried forever (nothing more is sent until the
     *  person turns sharing off and on). */
    @Test fun aRefusedKeyIsSaidAndNotRetried() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        failWith = ApiException(403, "403 Forbidden")
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(ShareRules.KEY_REFUSED, s.get(server)!!.lastError)
        assertEquals(ShareRules.KEY_REFUSED, com.domovoi.app.ui.screens.settings.shareStatusLine(s.get(server)))
        assertTrue(ShareRules.warns(s.get(server)))
        assertEquals("the house no longer accepts this phone's calendars: turn sharing off and on again", ShareRules.KEY_REFUSED)
        // The next passes (the change watch, the periodic one) send nothing.
        failWith = null
        val reads = calendarReads
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertTrue(puts.isEmpty())
        assertEquals(reads, calendarReads)
        // Off and on again: tried again.
        s.update(server) { ShareRules.turnOn(ShareRules.turnOff(it)) }
        sync(s).run(server)
        assertEquals(1, puts.size)
    }

    /** A 403 on the DELETE: retrying changes nothing; said, not looped. */
    @Test fun aRefusedDeleteIsSaidAndNotRetried() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.turnOff(ShareRules.pick(ShareRules.turnOn(it), listOf(12))) }
        failWith = ApiException(403, "403 Forbidden")
        assertEquals(ShareSync.Outcome.Done, sync(s).run(server))
        assertFalse(s.get(server)!!.pendingDelete)
        assertEquals(ShareRules.KEY_REFUSED_OFF, s.get(server)!!.notice)
        assertTrue(ShareRules.warns(s.get(server)))
    }

    /** Every PUT carries the generation it was read at (the epoch ms), and
     *  a 410 (made before sharing was turned off) or 409 (older than one
     *  stored) is dropped: not retried, not recorded as sent, no error. */
    @Test fun aSupersededOrTooLateUploadIsDroppedNotRetried() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        sync(s).run(server)
        assertEquals(now, puts.single()["generation"]!!.jsonPrimitive.long)
        val sentAt = s.get(server)!!.lastSentAtMs
        for (status in listOf(410, 409)) {
            now += 120_000
            rowsFor = { ids -> ids.map { InstanceRow(it * 100, it, now + 1000, now + 2000, false, "n$status", null) } }
            failWith = ApiException(status, "$status")
            assertEquals("$status is not retried", ShareSync.Outcome.Done, sync(s).run(server))
            val e = s.get(server)!!
            assertNull("$status is no error", e.lastError)
            assertEquals("$status is not a send", sentAt, e.lastSentAtMs)
            assertTrue(e.enabled)
        }
        assertFalse(ShareSync.retries(409))
        assertFalse(ShareSync.retries(410))
        // The next pass reads the phone again, with a newer generation.
        failWith = null
        now += 120_000
        sync(s).run(server)
        assertEquals(now, puts.last()["generation"]!!.jsonPrimitive.long)
    }

    /** The DELETE says the generation it was sent at, at or above any
     *  snapshot read before it (the server refuses those later, 410). */
    @Test fun theDeleteCarriesAGenerationAboveEverySnapshotBeforeIt() = runBlocking {
        val s = store()
        s.update(server) { ShareRules.pick(ShareRules.turnOn(it), listOf(12)) }
        sync(s).run(server)
        val read = puts.single()["generation"]!!.jsonPrimitive.long
        now += 1
        s.update(server) { ShareRules.turnOff(it) }
        sync(s).run(server)
        assertTrue(deleteGenerations.single() > read)
    }
}
