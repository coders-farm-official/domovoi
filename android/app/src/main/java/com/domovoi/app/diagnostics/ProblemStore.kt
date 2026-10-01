package com.domovoi.app.diagnostics

import java.io.File
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * The problem log on disk: `problems.json` in [dir], plus a `pending/` folder
 * a dying process drops its crash into.
 *
 * A crash is written by the crashing thread while the process is going down,
 * often out of memory, so it does the least possible work there: one small
 * file per crash, no reading or merging. The next launch folds pending files
 * into the log ([absorbPending]). Everything else goes through [update], a
 * locked read-modify-write that replaces the file atomically so a kill in the
 * middle leaves the previous log intact.
 */
class ProblemStore(private val dir: File) {
    private val logFile = File(dir, "problems.json")
    private val pendingDir = File(dir, "pending")
    private val lock = Any()

    fun read(): ProblemLog = synchronized(lock) { readUnlocked() }

    fun update(change: (ProblemLog) -> ProblemLog): ProblemLog = synchronized(lock) {
        val before = readUnlocked()
        val after = change(before)
        if (after != before) writeAtomically(logFile, encodeLog(after))
        after
    }

    /** Crash path: one file, no merge. Safe to call from any thread. */
    fun writePending(p: Problem) {
        pendingDir.mkdirs()
        writeAtomically(File(pendingDir, "${p.atMs}-${p.pid ?: 0}.json"), encodeProblem(p))
    }

    /** Fold crashes saved by earlier processes into the log. */
    fun absorbPending(): ProblemLog = synchronized(lock) {
        val files = pendingDir.listFiles { f -> f.isFile && f.name.endsWith(".json") }.orEmpty()
        if (files.isEmpty()) return@synchronized readUnlocked()
        var log = readUnlocked()
        files.sortedBy { it.name }.forEach { f ->
            decodeProblem(runCatching { f.readText() }.getOrNull())?.let { log = log.with(it) }
        }
        writeAtomically(logFile, encodeLog(log))
        files.forEach { it.delete() }
        log
    }

    fun clear() = synchronized(lock) {
        // Keep the exit-history mark: clearing must not re-import old exits.
        val mark = readUnlocked().exitsSeenUpToMs
        writeAtomically(logFile, encodeLog(ProblemLog(exitsSeenUpToMs = mark)))
        pendingDir.listFiles().orEmpty().forEach { it.delete() }
    }

    private fun readUnlocked(): ProblemLog =
        decodeLog(runCatching { if (logFile.isFile) logFile.readText() else null }.getOrNull())

    private fun writeAtomically(target: File, text: String) {
        target.parentFile?.mkdirs()
        val tmp = File(target.parentFile, target.name + ".tmp")
        tmp.writeText(text)
        try {
            Files.move(
                tmp.toPath(), target.toPath(),
                StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE,
            )
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(tmp.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
        }
    }
}
