package com.domovoi.app.alerts

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList

/**
 * The background sync's chain, with the sync's steps and the alarm faked:
 * how a tick is armed on each API level and when (asked for before anything
 * that could fail, and never where it would hold back a timer's own alarm),
 * what a tick runs, and what it skips.
 */
class TimerSyncTest {
    /** Everything that happened, in order: "arm@<ms from now>", "cancel", and the steps. */
    private val events = CopyOnWriteArrayList<String>()
    private val modes = CopyOnWriteArrayList<ChainMode>()
    private var wallNow = 1_790_000_000_000L
    private var elapsedNow = 5_000_000L
    private var server = true
    private var notifications = true
    private var lan = true
    private var mode = ChainMode.EXACT

    /** The mirrored timer alarms (wall clock). */
    private var mirror: List<Long> = emptyList()

    private fun advance(ms: Long) {
        wallNow += ms
        elapsedNow += ms
    }

    private inner class FakeWork : SyncWork {
        var answers = true
        var failCatchUp: Exception? = null
        var hangCatchUp = false
        var gate: CompletableDeferred<Unit>? = null
        override suspend fun rearm() { events += "rearm" }
        override suspend fun catchUp(): Boolean {
            events += "catchUp"
            gate?.await()
            failCatchUp?.let { throw it }
            if (hangCatchUp) awaitCancellation()
            return answers
        }
        override suspend fun syncMirror(): Boolean {
            events += "syncMirror"
            return answers || !notifications
        }
    }

    private inner class FakeAlarm : SyncAlarm {
        override fun mode() = mode
        override fun schedule(atElapsedMs: Long, mode: ChainMode) {
            events += "arm@${atElapsedMs - elapsedNow}"
            modes += mode
        }
        override fun cancel() { events += "cancel" }
    }

    private val work = FakeWork()

    private fun sync(budgetMs: Long = SYNC_BUDGET_MS) = TimerSync(
        work = work,
        alarm = FakeAlarm(),
        hasServer = { server },
        canPost = { notifications },
        onLan = { lan },
        mirrorTimes = { mirror },
        wall = { wallNow },
        elapsed = { elapsedNow },
        budgetMs = budgetMs,
    )

    private val lead = "arm@$SYNC_EXACT_LEAD_MS"
    private val min = 60_000L

    // ---- how, per API level ---------------------------------------------------------

    /** Exact wherever exact alarms are allowed: only an exact
     *  allow-while-idle alarm gets Doze's network allowance (measured on the
     *  API 35 emulator; see TimerSync.kt). */
    @Test fun theTickIsExactWhereverExactAlarmsAreAllowed() {
        // 26-30: exact needs no permission, but shares the timers' one slot per 9 minutes.
        assertEquals(ChainMode.EXACT_GUARDED, chainMode(26, canExact = true))
        assertEquals(ChainMode.EXACT_GUARDED, chainMode(30, canExact = false))
        // 31+: the timers' exact alarms and the tick share 72 an hour.
        assertEquals(ChainMode.EXACT, chainMode(31, canExact = true))
        assertEquals(ChainMode.EXACT, chainMode(33, canExact = true))
        assertEquals(ChainMode.EXACT, chainMode(35, canExact = true))
        // 31-32 with "Alarms & reminders" revoked: no exact alarms at all.
        assertEquals(ChainMode.PLAIN, chainMode(31, canExact = false))
        assertEquals(ChainMode.PLAIN, chainMode(32, canExact = false))
    }

    @Test fun theLeads() {
        assertEquals(15 * min, syncLeadMs(SyncReason.TICK, ChainMode.EXACT))
        assertEquals(15 * min, syncLeadMs(SyncReason.START, ChainMode.EXACT_GUARDED))
        assertEquals(15 * min, syncLeadMs(SyncReason.SERVER, ChainMode.EXACT))
        assertEquals(10 * min, syncLeadMs(SyncReason.TICK, ChainMode.PLAIN))
        for (m in ChainMode.values()) assertEquals(2 * min, syncLeadMs(SyncReason.BOOT, m))
    }

    /** What the docs promise: exact ticks every 15 minutes; a plain one
     *  (inexact: Android may hold it up to 75% of its lead) 10 to 17.5 —
     *  "about every 15 minutes" — and the budget fits Doze's 10 s network
     *  allowance. */
    @Test fun theCadenceTheDocsPromise() {
        assertTrue(SYNC_LEAD_MS + SYNC_LEAD_MS * 3 / 4 <= 18 * min)
        assertTrue(SYNC_LEAD_MS >= 10 * min)
        assertEquals(15 * min, SYNC_EXACT_LEAD_MS)
        assertTrue(SYNC_BUDGET_MS < 10_000L)
    }

    @Test fun clearOfAlarmsKeepsNineMinutesFromEveryTimerAlarm() {
        val t = 1_000_000_000L
        assertEquals(t, clearOfAlarms(t, emptyList()))
        assertEquals("an alarm 20 minutes on is no trouble", t, clearOfAlarms(t, listOf(t + 20 * min)))
        assertEquals("9 minutes before one is fine", t, clearOfAlarms(t, listOf(t + 9 * min)))
        assertEquals("9 minutes after one is fine", t, clearOfAlarms(t, listOf(t - 9 * min)))
        assertEquals("5 minutes before one: after it", t + 14 * min, clearOfAlarms(t, listOf(t + 5 * min)))
        assertEquals("5 minutes after one: where Doze would hold it", t + 4 * min, clearOfAlarms(t, listOf(t - 5 * min)))
        assertEquals("the same instant", t + 9 * min, clearOfAlarms(t, listOf(t)))
        assertEquals(
            "moved past one, into the next one's way, and past that too",
            t + 24 * min, clearOfAlarms(t, listOf(t + 15 * min, t + 2 * min, t + 60 * min)),
        )
        assertEquals(
            "moved past one, clear of the next",
            t + 11 * min, clearOfAlarms(t, listOf(t + 20 * min, t + 2 * min)),
        )
    }

    @Test fun onApi26To30TheTickStaysClearOfTheMirroredAlarms() = runBlocking {
        mode = ChainMode.EXACT_GUARDED
        mirror = listOf(wallNow + 17 * min)
        sync().arm(SyncReason.TICK)
        assertEquals(listOf("arm@${26 * min}"), events.toList())
        assertEquals(listOf(ChainMode.EXACT_GUARDED), modes.toList())
    }

    @Test fun fromApi31NothingIsMoved() = runBlocking {
        // 72 an hour between the tick and the timers; a plain tick takes no slot.
        mirror = listOf(wallNow + 12 * min, wallNow + 16 * min)
        mode = ChainMode.EXACT
        sync().arm(SyncReason.TICK)
        mode = ChainMode.PLAIN
        sync().arm(SyncReason.TICK)
        assertEquals(listOf(lead, "arm@$SYNC_LEAD_MS"), events.toList())
        assertEquals(listOf(ChainMode.EXACT, ChainMode.PLAIN), modes.toList())
    }

    @Test fun aTimerArmedAfterTheTickMovesTheTickOutOfItsWay() = runBlocking {
        mode = ChainMode.EXACT_GUARDED
        val s = sync()
        s.arm(SyncReason.TICK)
        assertEquals(listOf("arm@${15 * min}"), events.toList())
        // The app, open, mirrors a new timer due in 14 minutes.
        advance(1 * min)
        mirror = listOf(wallNow + 13 * min)
        s.reguard()
        assertEquals(listOf("arm@${15 * min}", "arm@${22 * min}"), events.toList())
        // Nothing new in the way: nothing moves.
        s.reguard()
        assertEquals(2, events.size)
        // From API 31 a reguard never moves anything.
        mode = ChainMode.EXACT
        mirror = listOf(wallNow + 21 * min)
        s.reguard()
        assertEquals(2, events.size)
    }

    // ---- when -------------------------------------------------------------------------

    @Test fun aTickAsksForTheNextOneBeforeItTouchesTheNetwork() = runBlocking {
        assertEquals(TickResult.SYNCED, sync().onTick())
        assertEquals(listOf(lead, "catchUp", "syncMirror"), events.toList())
    }

    @Test fun aTickThatFailsOrTimesOutStillKeepsTheChain() = runBlocking {
        work.failCatchUp = IllegalStateException("datastore broke")
        assertEquals(TickResult.UNREACHABLE, sync().onTick())
        assertEquals("the mirror still syncs after a failed catch-up",
            listOf(lead, "catchUp", "syncMirror"), events.toList())

        events.clear()
        work.failCatchUp = null
        work.hangCatchUp = true
        assertEquals(TickResult.TIMED_OUT, sync(budgetMs = 50).onTick())
        assertEquals(listOf(lead, "catchUp"), events.toList())
    }

    @Test fun anUnreachableServerIsReportedAndRetriedNextTick() = runBlocking {
        work.answers = false
        val s = sync()
        assertEquals(TickResult.UNREACHABLE, s.onTick())
        advance(1_000)
        assertEquals("an unanswered sync is not fresh", TickResult.UNREACHABLE, s.onTick())
        assertEquals(2, events.count { it == "catchUp" })
    }

    @Test fun noServerStopsTheChainAndAsksNothing() = runBlocking {
        server = false
        val s = sync()
        assertEquals(TickResult.NO_SERVER, s.onTick())
        s.arm(SyncReason.START)
        s.arm(SyncReason.SERVER)
        assertEquals(listOf("cancel", "cancel", "cancel"), events.toList())
    }

    @Test fun aServerSetLaterStartsTheChain() = runBlocking {
        server = false
        val s = sync()
        s.arm(SyncReason.START)
        server = true
        s.arm(SyncReason.SERVER)
        assertEquals(listOf("cancel", lead), events.toList())
    }

    @Test fun withNotificationsOffATickDisarmsWithoutAskingAndKeepsTheChain() = runBlocking {
        notifications = false
        assertEquals(TickResult.NOTIFICATIONS_OFF, sync().onTick())
        // syncMirror with notifications off cancels every mirrored alarm and
        // asks nothing (AlertEngineTest.withNotificationsOffNothingPosts...).
        assertEquals(listOf(lead, "syncMirror"), events.toList())
    }

    /** Security review 2026-09-30: the tick sends the household token, as
     *  plain http to a private address; off Wi-Fi (mobile data, or a
     *  foreign network reusing the home subnet) it asks nothing. */
    @Test fun offWifiATickAsksNothingAndKeepsTheChain() = runBlocking {
        lan = false
        val s = sync()
        assertEquals(TickResult.OFF_LAN, s.onTick())
        assertEquals(listOf(lead), events.toList())

        events.clear()
        assertEquals(TickResult.OFF_LAN, s.onStart())
        assertEquals("a start off Wi-Fi re-arms the mirror, asks nothing",
            listOf(lead, "rearm"), events.toList())

        events.clear()
        lan = true
        assertEquals(TickResult.SYNCED, s.onTick())
        assertEquals(listOf(lead, "catchUp", "syncMirror"), events.toList())
    }

    // ---- the start, and a tick right after it --------------------------------------

    @Test fun theStartArmsTheChainThenReArmsCatchesUpAndReMirrors() = runBlocking {
        assertEquals(TickResult.SYNCED, sync().onStart())
        assertEquals(listOf(lead, "rearm", "catchUp", "syncMirror"), events.toList())
    }

    @Test fun aTickRightAfterTheStartsSyncAsksNothing() = runBlocking {
        val s = sync()
        s.onStart()
        events.clear()
        advance(SYNC_FRESH_MS - 1)
        assertEquals(TickResult.FRESH, s.onTick())
        assertEquals(listOf(lead), events.toList())

        events.clear()
        advance(1)
        assertEquals(TickResult.SYNCED, s.onTick())
        assertEquals(listOf(lead, "catchUp", "syncMirror"), events.toList())
    }

    @Test fun aTickDuringTheStartsSyncWaitsForItThenAsksNothing() = runBlocking {
        val s = sync()
        work.gate = CompletableDeferred()
        val start = async(Dispatchers.Default) { s.onStart() }
        while ("catchUp" !in events) yield()
        val tick = async(Dispatchers.Default) { s.onTick() }
        while (events.count { it == lead } < 2) yield()
        work.gate!!.complete(Unit)
        assertEquals(TickResult.SYNCED, start.await())
        assertEquals(TickResult.FRESH, tick.await())
        assertEquals(1, events.count { it == "catchUp" })
    }

    @Test fun aStartThatCouldNotReachTheServerLeavesTheTickToTry() = runBlocking {
        val s = sync()
        work.answers = false
        assertEquals(TickResult.UNREACHABLE, s.onStart())
        work.answers = true
        advance(1_000)
        assertEquals(TickResult.SYNCED, s.onTick())
    }

    // ---- boot and app update --------------------------------------------------------

    @Test fun aBootAsksForATickSoonAndReArmsTheMirror() = runBlocking {
        sync().onBoot()
        assertEquals(listOf("arm@$SYNC_AFTER_BOOT_MS", "rearm"), events.toList())
    }

    /** At boot the process starts (TimerAlerts.start runs onStart) and the
     *  boot receiver runs, in either order: the boot's sooner tick wins. */
    @Test fun theBootsSoonerTickWinsWhicheverRunsFirst() = runBlocking {
        val bootFirst = sync()
        bootFirst.onBoot()
        bootFirst.onStart()
        assertEquals(listOf("arm@$SYNC_AFTER_BOOT_MS", "rearm", "rearm", "catchUp", "syncMirror"), events.toList())

        events.clear()
        val startFirst = sync()
        startFirst.onStart()
        startFirst.onBoot()
        assertEquals(listOf(lead, "rearm", "catchUp", "syncMirror", "arm@$SYNC_AFTER_BOOT_MS", "rearm"), events.toList())
    }

    /** The receivers are thin: each hands its broadcast to this class. */
    @Test fun theReceiversCallTheChain() {
        fun src(name: String) = listOf(
            java.io.File("src/main/java/com/domovoi/app/alerts/$name"),
            java.io.File("app/src/main/java/com/domovoi/app/alerts/$name"),
        ).first { it.isFile }.readText()
        assertTrue("boot and app update", "alerts.sync.onBoot()" in src("TimerBootReceiver.kt"))
        val tick = src("TimerSync.kt").substringAfter("class TimerSyncReceiver")
        assertTrue("the tick", "alerts.sync.onTick()" in tick)
        val alerts = src("TimerAlerts.kt")
        assertTrue("a server switch re-arms the chain", "sync.arm(SyncReason.SERVER)" in alerts)
        assertTrue("a mirror sync while open re-guards the tick", "sync.reguard()" in alerts)
    }
}
