package com.domovoi.app.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.TimeZone

class ProblemLogTest {

    private val utc = TimeZone.getTimeZone("UTC")

    private fun crash(at: Long, pid: Int = 100) =
        Problem(ProblemKind.CRASH, at, "Crashed: boom", pid = pid, trace = "java.lang.X\n\tat a.B.c(B.java:1)")

    private fun freeze(at: Long) = Problem(ProblemKind.FREEZE, at, "froze", pid = 1)

    private fun exit(reason: Int, at: Long, pid: Int = 200, importance: Int = 100, trace: String? = null) =
        ExitRecord(reason, at, pid, description = "desc $reason", importance = importance,
            pssKb = 150_000, rssKb = 245_760, trace = trace)

    // ── Retention ─────────────────────────────────────────────────────

    @Test fun newestFirstAndCapped() {
        var log = ProblemLog()
        (1..15).forEach { log = log.with(crash(it * 1_000L, pid = it)) }
        assertEquals(ProblemLog.MAX_KEPT, log.problems.size)
        assertEquals(15_000L, log.problems.first().atMs)
        assertEquals(6_000L, log.problems.last().atMs)
    }

    @Test fun sameProblemIsNotAddedTwice() {
        val log = ProblemLog().with(crash(5)).with(crash(5))
        assertEquals(1, log.problems.size)
    }

    @Test fun freezesCannotPushACrashOut() {
        var log = ProblemLog().with(crash(1))
        (2..30L).forEach { log = log.with(freeze(it)) }
        assertEquals(ProblemLog.MAX_FREEZES, log.problems.count { it.kind == ProblemKind.FREEZE })
        assertTrue(log.problems.any { it.kind == ProblemKind.CRASH })
        // The freezes kept are the newest.
        assertEquals(30L, log.problems.first().atMs)
    }

    @Test fun replacingUpdatesAFreezeWhenItEnds() {
        val open = freezeProblem(3_100, "  at a.B.c(B.java:1)", atMs = 50, pid = 7)
        val log = ProblemLog().with(open).replacing(open.recovered(4_600))
        assertEquals(1, log.problems.size)
        assertEquals("The screen stopped responding for 4.6 s, then recovered", log.problems[0].summary)
        assertEquals(open.trace, log.problems[0].trace)
    }

    // ── Exit history ──────────────────────────────────────────────────

    @Test fun onlyProblemExitsCount() {
        assertEquals(ProblemKind.CRASH, exitKind(ExitReason.CRASH, 100))
        assertEquals(ProblemKind.NATIVE_CRASH, exitKind(ExitReason.CRASH_NATIVE, 400))
        assertEquals(ProblemKind.ANR, exitKind(ExitReason.ANR, 100))
        assertEquals(ProblemKind.START_FAILED, exitKind(ExitReason.INITIALIZATION_FAILURE, 100))
        assertEquals(ProblemKind.RESOURCES, exitKind(ExitReason.EXCESSIVE_RESOURCE_USAGE, 400))
        assertEquals(ProblemKind.FROZEN_KILL, exitKind(ExitReason.FREEZER, 400))
        // Memory: a problem while in use (on screen or playing), routine when cached.
        assertEquals(ProblemKind.LOW_MEMORY, exitKind(ExitReason.LOW_MEMORY, 100))
        assertEquals(ProblemKind.LOW_MEMORY, exitKind(ExitReason.LOW_MEMORY, 125))
        assertNull(exitKind(ExitReason.LOW_MEMORY, 400))
        // Swiped away (10), signalled (2), exited normally (1), app updated (15): not problems.
        listOf(0, 1, 2, 10, 11, 13, 15).forEach { assertNull("reason $it", exitKind(it, 100)) }
    }

    @Test fun anrExitBecomesAProblemWithItsMainThread() {
        val dump = "Subject: Input dispatching timed out\n\"main\" prio=5 tid=1 Runnable\n" +
            "  | group=\"main\"\n  at com.domovoi.app.ui.screens.music.PlayerTabKt.PlayerPanel(PlayerTab.kt:424)\n"
        val p = exit(ExitReason.ANR, 1_000, pid = 12_222, trace = dump).toProblem()!!
        assertEquals(ProblemKind.ANR, p.kind)
        assertEquals(SOURCE_ANDROID, p.source)
        assertEquals(12_222, p.pid)
        assertTrue(p.description!!.contains("desc 6"))
        assertTrue(p.description!!.contains("pid 12222, on screen, memory 240 MB"))
        assertTrue(p.trace!!.contains("PlayerTab.kt:424"))
        assertFalse(p.trace!!.contains("| group"))
    }

    @Test fun importTakesNewExitsAndRemembersHowFarItGot() {
        val exits = listOf(
            exit(ExitReason.CRASH, 1_000),
            exit(10 /* user request */, 2_000),
            exit(ExitReason.ANR, 3_000),
        )
        val log = ProblemLog().importingExits(exits)
        assertEquals(listOf(ProblemKind.ANR, ProblemKind.CRASH), log.problems.map { it.kind })
        assertEquals(3_000L, log.exitsSeenUpToMs)
        // The next launch sees the same history plus one new exit.
        val again = log.importingExits(exits + exit(ExitReason.CRASH, 4_000, pid = 300))
        assertEquals(3, again.problems.size)
        assertEquals(4_000L, again.exitsSeenUpToMs)
        // Nothing new: unchanged.
        assertSame(again, again.importingExits(exits))
    }

    @Test fun normalExitsStillMoveTheMark() {
        val log = ProblemLog().importingExits(listOf(exit(10, 9_000)))
        assertTrue(log.problems.isEmpty())
        assertEquals(9_000L, log.exitsSeenUpToMs)
    }

    @Test fun appSavedCrashWinsOverAndroidsRecordOfTheSameDeath() {
        val saved = crash(at = 10_000, pid = 12_222)
        val log = ProblemLog().with(saved)
            .importingExits(listOf(exit(ExitReason.CRASH, 10_400, pid = 12_222)))
        assertEquals(listOf(saved), log.problems)
        assertEquals(10_400L, log.exitsSeenUpToMs)
    }

    @Test fun samePidLongAfterIsADifferentDeath() {
        val log = ProblemLog().with(crash(at = 10_000, pid = 12_222))
            .importingExits(listOf(exit(ExitReason.CRASH, 10_000 + SAME_DEATH_WINDOW_MS + 1, pid = 12_222)))
        assertEquals(2, log.problems.size)
    }

    @Test fun anrIsKeptNextToTheFreezeThatPrecededIt() {
        val f = freezeProblem(3_000, "  at x.Y.z(Y.java:1)", atMs = 1_000, pid = 12_222)
        val log = ProblemLog().with(f).importingExits(listOf(exit(ExitReason.ANR, 6_000, pid = 12_222)))
        assertEquals(listOf(ProblemKind.ANR, ProblemKind.FREEZE), log.problems.map { it.kind })
    }

    // ── Storage format ────────────────────────────────────────────────

    @Test fun logRoundTrips() {
        val log = ProblemLog().with(crash(1)).with(freeze(2)).copy(exitsSeenUpToMs = 77)
        assertEquals(log, decodeLog(encodeLog(log)))
        val p = crash(3)
        assertEquals(p, decodeProblem(encodeProblem(p)))
    }

    @Test fun damagedFileReadsAsEmpty() {
        assertEquals(ProblemLog(), decodeLog(null))
        assertEquals(ProblemLog(), decodeLog(""))
        assertEquals(ProblemLog(), decodeLog("{\"problems\": [ {\"kind\": "))
        assertEquals(ProblemLog(), decodeLog("not json"))
        assertNull(decodeProblem("{}"))
        // Unknown fields from a newer build are ignored.
        assertEquals(77L, decodeLog("{\"exitsSeenUpToMs\":77,\"future\":true}").exitsSeenUpToMs)
    }

    // ── Report ────────────────────────────────────────────────────────

    @Test fun reportHasHeaderThenEachProblemNewestFirst() {
        val problems = ProblemLog().with(crash(1_000)).with(freeze(2_000)).problems
        val text = renderReport(problems, listOf("app 1.0.0 (1, debug build)", "Android 15 (API 35), Google Pixel 7"), utc)
        val lines = text.lines()
        assertEquals("domovoi Android app: problem report", lines[0])
        assertEquals("app 1.0.0 (1, debug build)", lines[1])
        assertTrue(text.contains("--- 1 of 2 ---\nfreeze at 1970-01-01 00:00:02 +0000 (recorded by the app)\nfroze"))
        assertTrue(text.contains("--- 2 of 2 ---\ncrash at 1970-01-01 00:00:01 +0000 (recorded by the app)\nCrashed: boom"))
        assertTrue(text.contains("\tat a.B.c(B.java:1)"))
        assertTrue(text.indexOf("freeze at") < text.indexOf("crash at"))
    }

    @Test fun emptyReportSaysSo() {
        assertTrue(renderReport(emptyList(), listOf("h"), utc).endsWith("No problems recorded.\n"))
    }

    @Test fun androidRecordsSayWhereTheyCameFrom() {
        val p = exit(ExitReason.ANR, 0).toProblem()!!
        assertTrue(renderProblem(p, utc).startsWith("not responding at 1970-01-01 00:00:00 +0000 (recorded by Android)"))
    }
}
