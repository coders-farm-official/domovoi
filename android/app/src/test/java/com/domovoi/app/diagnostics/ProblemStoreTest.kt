package com.domovoi.app.diagnostics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class ProblemStoreTest {

    @get:Rule val tmp = TemporaryFolder()

    private fun crash(at: Long, pid: Int = 1) = Problem(ProblemKind.CRASH, at, "Crashed: x", pid = pid)

    @Test fun emptyStoreReadsEmpty() {
        assertEquals(ProblemLog(), ProblemStore(tmp.root).read())
    }

    @Test fun updatesPersistAcrossInstances() {
        val dir = File(tmp.root, "diagnostics")
        ProblemStore(dir).update { it.with(crash(5)) }
        assertEquals(listOf(crash(5)), ProblemStore(dir).read().problems)
        // No temp file left behind by the atomic replace.
        assertFalse(File(dir, "problems.json.tmp").exists())
    }

    @Test fun pendingCrashesAreFoldedInOnceThenRemoved() {
        val store = ProblemStore(tmp.root)
        store.update { it.with(crash(1)) }
        // Two dying processes, each dropping its crash without reading the log.
        store.writePending(crash(2, pid = 22))
        store.writePending(crash(3, pid = 33))
        assertEquals(1, store.read().problems.size)

        val log = store.absorbPending()
        assertEquals(listOf(3L, 2L, 1L), log.problems.map { it.atMs })
        assertTrue(File(tmp.root, "pending").listFiles().orEmpty().isEmpty())
        // Absorbing again changes nothing.
        assertEquals(log, store.absorbPending())
    }

    @Test fun damagedPendingFileIsDroppedNotFatal() {
        val store = ProblemStore(tmp.root)
        File(tmp.root, "pending").mkdirs()
        File(tmp.root, "pending/1-1.json").writeText("{ truncated")
        store.writePending(crash(9))
        assertEquals(listOf(9L), store.absorbPending().problems.map { it.atMs })
    }

    @Test fun damagedLogStartsOver() {
        File(tmp.root, "problems.json").writeText("\u0000\u0000garbage")
        val store = ProblemStore(tmp.root)
        assertEquals(ProblemLog(), store.read())
        store.update { it.with(crash(4)) }
        assertEquals(1, store.read().problems.size)
    }

    @Test fun clearKeepsTheExitMarkSoOldExitsAreNotReimported() {
        val store = ProblemStore(tmp.root)
        store.update { it.with(crash(1)).copy(exitsSeenUpToMs = 5_000) }
        store.writePending(crash(2))
        store.clear()
        val log = store.read()
        assertTrue(log.problems.isEmpty())
        assertEquals(5_000L, log.exitsSeenUpToMs)
        assertTrue(store.absorbPending().problems.isEmpty())
        // The same exit history on the next launch adds nothing back.
        val exits = listOf(ExitRecord(ExitReason.CRASH, 4_000, 9, null, 100, 0, 0))
        assertTrue(store.update { it.importingExits(exits) }.problems.isEmpty())
    }
}
