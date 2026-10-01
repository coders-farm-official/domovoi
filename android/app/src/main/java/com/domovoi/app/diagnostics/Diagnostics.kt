package com.domovoi.app.diagnostics

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Build
import android.os.Process
import android.os.StrictMode
import androidx.annotation.RequiresApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.io.File

/**
 * Crash, ANR and freeze capture: the app's own record of what went wrong, so
 * the next freeze or crash can be reported from the phone instead of
 * reconstructed on an emulator.
 *
 * - [install] (first thing in Application.onCreate) puts a crash recorder in
 *   front of the default uncaught-exception handler.
 * - [onLaunch] folds in crashes earlier processes saved and imports Android's
 *   exit history (API 30+), which also holds deaths from before this code
 *   existed: the history survives an in-place APK update.
 * - [watchdog] notices main-thread freezes while the app is on screen.
 *
 * The log stays in app storage (excluded from backup and device transfer
 * like everything else, see res/xml/data_extraction_rules.xml). Nothing is
 * uploaded and nothing is shown on the lock screen; the owner reads, copies
 * or shares it from Settings > About.
 */
object Diagnostics {
    private var store: ProblemStore? = null
    private val io = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _problems = MutableStateFlow<List<Problem>>(emptyList())
    val problems: StateFlow<List<Problem>> = _problems

    /** Headroom for the crash recorder: released first thing when a crash
     *  arrives, so an OutOfMemoryError still leaves room to save itself. */
    @Volatile private var reserve: ByteArray? = null

    /** The freeze being reported right now, updated when it ends. */
    @Volatile private var openFreeze: Problem? = null

    fun install(context: Context) {
        if (store != null) return
        store = ProblemStore(File(context.filesDir, "diagnostics"))
        reserve = ByteArray(256 * 1024)
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            recordCrash(thread, error)
            if (previous != null) {
                previous.uncaughtException(thread, error)
            } else {
                Process.killProcess(Process.myPid())
                System.exit(10)
            }
        }
    }

    private fun recordCrash(thread: Thread, error: Throwable) {
        reserve = null
        val s = store ?: return
        // Dying anyway: let the debug StrictMode policy pass this write.
        val policy = runCatching { StrictMode.allowThreadDiskWrites() }.getOrNull()
        try {
            s.writePending(
                crashProblem(
                    threadName = thread.name,
                    stack = error.stackTraceToString(),
                    firstLine = error.toString(),
                    atMs = System.currentTimeMillis(),
                    pid = Process.myPid(),
                ),
            )
        } catch (_: Throwable) {
            // Out of memory or disk: Android's exit history still records
            // the crash, without the stack.
        } finally {
            policy?.let { runCatching { StrictMode.setThreadPolicy(it) } }
        }
    }

    /** On every process start, off the main thread. */
    fun onLaunch(context: Context) {
        val s = store ?: return
        val app = context.applicationContext
        io.launch {
            runCatching { s.absorbPending() }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                runCatching {
                    val seen = s.read().exitsSeenUpToMs
                    val exits = exitHistory(app, seen)
                    if (exits.isNotEmpty()) s.update { it.importingExits(exits) }
                }
            }
            publish()
        }
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun exitHistory(context: Context, seenUpToMs: Long): List<ExitRecord> {
        val am = context.getSystemService(ActivityManager::class.java) ?: return emptyList()
        return am.getHistoricalProcessExitReasons(context.packageName, 0, 0)
            .filter { it.timestamp > seenUpToMs }
            .map { info ->
                ExitRecord(
                    reason = info.reason,
                    timestampMs = info.timestamp,
                    pid = info.pid,
                    description = info.description,
                    importance = info.importance,
                    pssKb = info.pss,
                    rssKb = info.rss,
                    // Only an ANR carries a readable text trace (a native
                    // crash's is a tombstone protobuf).
                    trace = if (info.reason == ApplicationExitInfo.REASON_ANR) readTrace(info) else null,
                    status = info.status,
                )
            }
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun readTrace(info: ApplicationExitInfo): String? = runCatching {
        // The main thread comes first in the dump; no need for every thread.
        info.traceInputStream?.use { stream ->
            val buf = CharArray(TRACE_READ_LIMIT)
            val reader = stream.bufferedReader()
            var n = 0
            while (n < buf.size) {
                val r = reader.read(buf, n, buf.size - n)
                if (r < 0) break
                n += r
            }
            String(buf, 0, n)
        }
    }.getOrNull()

    /** Re-read the log (and anything pending) for the settings screen. */
    fun refresh() {
        val s = store ?: return
        io.launch {
            runCatching { s.absorbPending() }
            publish()
        }
    }

    fun clear() {
        val s = store ?: return
        io.launch {
            runCatching { s.clear() }
            publish()
        }
    }

    private fun publish() {
        _problems.value = runCatching { store?.read()?.problems }.getOrNull().orEmpty()
    }

    // ── Freezes ───────────────────────────────────────────────────────

    val watchdog = MainThreadWatchdog(
        onFreeze = { blockedMs, stack -> recordFreeze(blockedMs, stack) },
        onRecovered = { total -> recordRecovery(total) },
    )

    private fun recordFreeze(blockedMs: Long, stack: List<String>) {
        val s = store ?: return
        val p = freezeProblem(
            blockedMs = blockedMs,
            trace = formatFrames(stack),
            atMs = System.currentTimeMillis(),
            pid = Process.myPid(),
        )
        openFreeze = p
        runCatching { s.update { it.with(p) } }
        publish()
    }

    private fun recordRecovery(totalBlockedMs: Long) {
        val s = store ?: return
        val p = openFreeze ?: return
        openFreeze = null
        runCatching { s.update { it.replacing(p.recovered(totalBlockedMs)) } }
        publish()
    }

    // ── Report header ─────────────────────────────────────────────────

    /** App and phone lines for the top of a shared report. No server
     *  address, token or account: only what helps read a stack. */
    fun reportHeader(context: Context): List<String> {
        val lines = mutableListOf<String>()
        runCatching {
            @Suppress("DEPRECATION")
            val info = context.packageManager.getPackageInfo(context.packageName, 0)
            val debug = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
            @Suppress("DEPRECATION")
            val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else info.versionCode.toLong()
            lines += "app ${info.versionName} ($code${if (debug) ", debug build" else ""}), " +
                "installed or updated ${formatTime(info.lastUpdateTime)}"
        }
        lines += "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}), " +
            "${Build.MANUFACTURER} ${Build.MODEL}"
        lines += "report made ${formatTime(System.currentTimeMillis())}"
        return lines
    }

    /** How much of an ANR dump is read: the main thread is near the top. */
    private const val TRACE_READ_LIMIT = 256 * 1024
}
