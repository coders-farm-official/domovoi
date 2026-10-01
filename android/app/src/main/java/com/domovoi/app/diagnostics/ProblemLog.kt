package com.domovoi.app.diagnostics

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.math.abs

/*
 * The app's own record of what went wrong on this phone: crashes, ANRs ("app
 * isn't responding" kills) and long main-thread freezes. Kept in app storage
 * only, never uploaded; the owner copies or shares it from Settings > About.
 *
 * Everything in this file is plain Kotlin so it runs in the JVM unit tests;
 * the Android glue (exit history, the crash handler, the watchdog thread) is
 * in Diagnostics.kt and MainThreadWatchdog.kt.
 */

/** One thing that went wrong. */
@Serializable
data class Problem(
    /** One of [ProblemKind]. */
    val kind: String,
    /** When it happened, epoch milliseconds. */
    val atMs: Long,
    /** One line saying what happened. */
    val summary: String,
    /** Android's description and the process details, when there are any. */
    val description: String? = null,
    /** A trimmed stack: the crashing thread's, or the main thread's for an
     *  ANR or a freeze. */
    val trace: String? = null,
    /** The process that died or froze; matches an app-saved crash to
     *  Android's record of the same death. */
    val pid: Int? = null,
    /** [SOURCE_APP] when the app wrote it itself, [SOURCE_ANDROID] when it
     *  came from Android's exit history. */
    val source: String = SOURCE_APP,
)

const val SOURCE_APP = "app"
const val SOURCE_ANDROID = "android"

object ProblemKind {
    const val CRASH = "crash"
    const val NATIVE_CRASH = "native crash"
    const val ANR = "not responding"
    const val FREEZE = "freeze"
    const val LOW_MEMORY = "killed for memory"
    const val START_FAILED = "failed to start"
    const val RESOURCES = "killed for resource use"
    const val FROZEN_KILL = "killed while frozen"
    const val KILLED = "ended abruptly"
}

/** The stored log: newest first. */
@Serializable
data class ProblemLog(
    val problems: List<Problem> = emptyList(),
    /** Newest exit-history timestamp already imported; anything at or before
     *  it was looked at on an earlier launch. */
    val exitsSeenUpToMs: Long = 0,
) {
    companion object {
        /** How many problems are kept. */
        const val MAX_KEPT = 10

        /** Freezes are kept to at most this many, so a run of them cannot
         *  push a crash or ANR out of the log. */
        const val MAX_FREEZES = 4
    }
}

/** Add [p], newest first, trimmed to [ProblemLog.MAX_KEPT] (and the freeze
 *  cap). A problem already in the log (same kind, time and process) is not
 *  added twice. */
fun ProblemLog.with(p: Problem): ProblemLog {
    if (problems.any { it.kind == p.kind && it.atMs == p.atMs && it.pid == p.pid }) return this
    return copy(problems = trimmed(problems + p))
}

/** Replace the problem matching [p]'s kind, time and process; added if absent. */
fun ProblemLog.replacing(p: Problem): ProblemLog {
    val kept = problems.filterNot { it.kind == p.kind && it.atMs == p.atMs && it.pid == p.pid }
    return copy(problems = trimmed(kept + p))
}

private fun trimmed(all: List<Problem>): List<Problem> {
    val newestFirst = all.sortedByDescending { it.atMs }
    var freezes = 0
    return newestFirst
        .filter { it.kind != ProblemKind.FREEZE || ++freezes <= ProblemLog.MAX_FREEZES }
        .take(ProblemLog.MAX_KEPT)
}

// ---------------------------------------------------------------------------
// Android's exit history (ActivityManager.getHistoricalProcessExitReasons)
// ---------------------------------------------------------------------------

/** The parts of an ApplicationExitInfo this app keeps, as plain data. */
data class ExitRecord(
    val reason: Int,
    val timestampMs: Long,
    val pid: Int,
    val description: String?,
    val importance: Int,
    val pssKb: Long,
    val rssKb: Long,
    /** The ANR trace text for an ANR; null otherwise. */
    val trace: String? = null,
    /** ApplicationExitInfo.getStatus: the signal number for a signalled exit. */
    val status: Int = 0,
)

/** ApplicationExitInfo.REASON_* values (stable platform constants; the
 *  newer ones are not in every SDK this compiles against). */
object ExitReason {
    const val SIGNALED = 2
    const val LOW_MEMORY = 3
    const val CRASH = 4
    const val CRASH_NATIVE = 5
    const val ANR = 6
    const val INITIALIZATION_FAILURE = 7
    const val EXCESSIVE_RESOURCE_USAGE = 9
    const val FREEZER = 14
}

/** RunningAppProcessInfo.IMPORTANCE_FOREGROUND_SERVICE: music playing in the
 *  background still counts as in use. */
private const val IMPORTANCE_IN_USE = 125

/** Which exits are problems worth showing, and what to call them. A normal
 *  exit (swiped away, process cached and reclaimed, app updated) is not. */
fun exitKind(reason: Int, importance: Int): String? = when (reason) {
    ExitReason.CRASH -> ProblemKind.CRASH
    ExitReason.CRASH_NATIVE -> ProblemKind.NATIVE_CRASH
    ExitReason.ANR -> ProblemKind.ANR
    ExitReason.INITIALIZATION_FAILURE -> ProblemKind.START_FAILED
    ExitReason.EXCESSIVE_RESOURCE_USAGE -> ProblemKind.RESOURCES
    ExitReason.FREEZER -> ProblemKind.FROZEN_KILL
    // Killed by a signal nobody in Android asked for: in practice a crash
    // that could not report itself. An OutOfMemoryError off the main thread
    // cannot even log, so the crash handler's last resort kills the process
    // and Android records reason 2, status 9, not a crash (seen on the
    // emulator with the 2026-09-30 long-queue crash).
    ExitReason.SIGNALED -> ProblemKind.KILLED
    // Reclaiming a cached app is routine; killing one in use is not.
    ExitReason.LOW_MEMORY -> if (importance <= IMPORTANCE_IN_USE) ProblemKind.LOW_MEMORY else null
    else -> null
}

private fun importanceLabel(importance: Int): String = when {
    importance <= 100 -> "on screen"
    importance <= IMPORTANCE_IN_USE -> "playing in the background"
    importance <= 230 -> "visible"
    else -> "in the background"
}

fun ExitRecord.toProblem(): Problem? {
    val kind = exitKind(reason, importance) ?: return null
    val details = buildString {
        description?.takeIf { it.isNotBlank() }?.let { append(it.trim()).append('\n') }
        append("pid ").append(pid).append(", ").append(importanceLabel(importance))
        if (rssKb > 0) append(", memory ").append(rssKb / 1024).append(" MB")
        append(" (Android exit reason ").append(reason).append(')')
    }
    return Problem(
        kind = kind,
        atMs = timestampMs,
        summary = when (kind) {
            ProblemKind.ANR -> "Android closed the app because it stopped responding"
            ProblemKind.CRASH -> "The app crashed"
            ProblemKind.NATIVE_CRASH -> "The app crashed in native code"
            ProblemKind.LOW_MEMORY -> "Android closed the app while it was in use, to free memory"
            ProblemKind.START_FAILED -> "The app failed to start"
            ProblemKind.RESOURCES -> "Android closed the app for using too many resources"
            ProblemKind.KILLED -> "The app ended abruptly" +
                (if (status > 0) " (signal $status)" else "") +
                ", usually a crash it could not report, such as running out of memory"
            else -> "Android closed the app while it was frozen"
        },
        description = details,
        trace = trace?.let { AnrTrace.mainThread(it) },
        pid = pid,
        source = SOURCE_ANDROID,
    )
}

/** How far apart the app's own crash record and Android's record of the
 *  same death can be: the app saves first, Android stamps the exit after
 *  the process is gone. */
const val SAME_DEATH_WINDOW_MS = 60_000L

/**
 * Fold Android's exit history into the log. Exits at or before
 * [ProblemLog.exitsSeenUpToMs] were imported on an earlier launch and are
 * skipped. A crash the app already saved itself (same process) keeps the
 * app's copy, which has the stack Android's record lacks.
 */
fun ProblemLog.importingExits(exits: List<ExitRecord>): ProblemLog {
    val fresh = exits.filter { it.timestampMs > exitsSeenUpToMs }
    if (fresh.isEmpty()) return this
    var log = this
    for (exit in fresh.sortedBy { it.timestampMs }) {
        val p = exit.toProblem() ?: continue
        // Same process, and close in time: a pid is reused eventually.
        val savedByApp = log.problems.any {
            it.source == SOURCE_APP && it.kind == ProblemKind.CRASH && it.pid == exit.pid &&
                abs(it.atMs - exit.timestampMs) <= SAME_DEATH_WINDOW_MS &&
                (p.kind == ProblemKind.CRASH || p.kind == ProblemKind.NATIVE_CRASH ||
                    p.kind == ProblemKind.KILLED)
        }
        if (!savedByApp) log = log.with(p)
    }
    return log.copy(exitsSeenUpToMs = maxOf(exitsSeenUpToMs, fresh.maxOf { it.timestampMs }))
}

// ---------------------------------------------------------------------------
// Traces
// ---------------------------------------------------------------------------

object AnrTrace {
    /**
     * The main thread's part of an ANR trace dump, trimmed. The dump lists
     * every thread; the main one is what was stuck. Thread bookkeeping lines
     * ("| group=...") are dropped, native frames are kept only up to a few,
     * and the "Subject:" line, when present, says what timed out.
     */
    fun mainThread(raw: String, maxFrames: Int = 40, maxNative: Int = 6): String? {
        val lines = raw.lines()
        val subject = lines.firstOrNull { it.startsWith("Subject:") }?.trim()
        val start = lines.indexOfFirst { it.startsWith("\"main\"") }
        if (start < 0) return subject
        val out = mutableListOf<String>()
        subject?.let { out += it }
        out += lines[start].trim()
        var frames = 0
        var natives = 0
        var dropped = 0
        for (line in lines.drop(start + 1)) {
            if (line.isBlank() || line.startsWith("\"")) break
            val t = line.trim()
            when {
                t.startsWith("|") -> Unit
                t.startsWith("native:") -> if (natives < maxNative) { out += "  $t"; natives++ } else dropped++
                frames < maxFrames -> { out += "  $t"; frames++ }
                else -> dropped++
            }
        }
        if (dropped > 0) out += "  ... $dropped more"
        return out.joinToString("\n")
    }
}

/**
 * A Java stack trace, trimmed: each section (the exception, then every
 * "Caused by:") keeps its header and first [framesPerSection] frames.
 */
fun trimStack(text: String, framesPerSection: Int = 30, maxChars: Int = 16_000): String {
    val out = StringBuilder()
    var frames = 0
    var dropped = 0
    fun flushDropped() {
        if (dropped > 0) out.append("\t... ").append(dropped).append(" more\n")
        dropped = 0
    }
    for (line in text.lines()) {
        if (line.isBlank()) continue
        val t = line.trimStart()
        val isFrame = t.startsWith("at ") || (t.startsWith("...") && t.endsWith("more"))
        if (!isFrame) {
            flushDropped()
            frames = 0
            out.append(line).append('\n')
        } else if (t.startsWith("...")) {
            out.append(line).append('\n')
        } else if (frames < framesPerSection) {
            out.append(line).append('\n')
            frames++
        } else {
            dropped++
        }
    }
    flushDropped()
    val s = out.toString().trimEnd()
    return if (s.length <= maxChars) s else s.take(maxChars) + "\n\t... (cut)"
}

/** A crash the app caught itself (Thread.UncaughtExceptionHandler). */
fun crashProblem(threadName: String, stack: String, firstLine: String, atMs: Long, pid: Int): Problem =
    Problem(
        kind = ProblemKind.CRASH,
        atMs = atMs,
        summary = "Crashed: " + firstLine.take(300),
        description = "thread \"$threadName\", pid $pid",
        trace = trimStack(stack),
        pid = pid,
        source = SOURCE_APP,
    )

/** A main-thread freeze the watchdog saw, while it is still going on. */
fun freezeProblem(blockedMs: Long, trace: String?, atMs: Long, pid: Int): Problem =
    Problem(
        kind = ProblemKind.FREEZE,
        atMs = atMs,
        summary = "The screen stopped responding for ${seconds(blockedMs)} (still frozen when saved)",
        description = "main thread blocked; pid $pid",
        trace = trace,
        pid = pid,
        source = SOURCE_APP,
    )

/** The same freeze once it is over. */
fun Problem.recovered(totalBlockedMs: Long): Problem =
    copy(summary = "The screen stopped responding for ${seconds(totalBlockedMs)}, then recovered")

internal fun seconds(ms: Long): String = String.format(Locale.US, "%.1f s", ms / 1000.0)

/** Format a main-thread stack sampled by the watchdog. */
fun formatFrames(frames: List<String>, max: Int = 40): String {
    val shown = frames.take(max).joinToString("\n") { "  at $it" }
    return if (frames.size > max) "$shown\n  ... ${frames.size - max} more" else shown
}

// ---------------------------------------------------------------------------
// Storage format and the shareable report
// ---------------------------------------------------------------------------

private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

fun encodeLog(log: ProblemLog): String = json.encodeToString(ProblemLog.serializer(), log)

/** A damaged or foreign file reads as an empty log rather than failing. */
fun decodeLog(text: String?): ProblemLog =
    if (text.isNullOrBlank()) ProblemLog()
    else runCatching { json.decodeFromString(ProblemLog.serializer(), text) }.getOrDefault(ProblemLog())

fun encodeProblem(p: Problem): String = json.encodeToString(Problem.serializer(), p)

fun decodeProblem(text: String?): Problem? =
    if (text.isNullOrBlank()) null
    else runCatching { json.decodeFromString(Problem.serializer(), text) }.getOrNull()

fun formatTime(ms: Long, zone: TimeZone = TimeZone.getDefault()): String =
    SimpleDateFormat("yyyy-MM-dd HH:mm:ss Z", Locale.US).apply { timeZone = zone }.format(Date(ms))

/** One problem as text, for copying. */
fun renderProblem(p: Problem, zone: TimeZone = TimeZone.getDefault()): String = buildString {
    append(p.kind).append(" at ").append(formatTime(p.atMs, zone))
    append(if (p.source == SOURCE_ANDROID) " (recorded by Android)" else " (recorded by the app)")
    append('\n').append(p.summary)
    p.description?.takeIf { it.isNotBlank() }?.let { append('\n').append(it) }
    p.trace?.takeIf { it.isNotBlank() }?.let { append("\n\n").append(it) }
}

/** The whole log as one report: [header] lines (app and phone), then each
 *  problem, newest first. */
fun renderReport(problems: List<Problem>, header: List<String>, zone: TimeZone = TimeZone.getDefault()): String =
    buildString {
        append("domovoi Android app: problem report\n")
        header.forEach { append(it).append('\n') }
        if (problems.isEmpty()) {
            append("\nNo problems recorded.\n")
            return@buildString
        }
        problems.forEachIndexed { i, p ->
            append("\n--- ").append(i + 1).append(" of ").append(problems.size).append(" ---\n")
            append(renderProblem(p, zone)).append('\n')
        }
    }
