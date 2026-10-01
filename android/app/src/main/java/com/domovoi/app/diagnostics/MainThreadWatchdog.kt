package com.domovoi.app.diagnostics

import android.os.Debug
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/**
 * Notices the main thread freezing while the app is on screen, and saves
 * what it was doing.
 *
 * Android records an ANR only when it kills the app, and a crash only when
 * the app dies. A freeze that ends on its own (the 2026-09-30 report: the
 * screen froze, taps queued up, then everything "snapped" through at once)
 * leaves no trace anywhere, so this thread pings the main thread and, when a
 * ping goes unanswered for [thresholdMs], samples the main thread's stack and
 * saves a [ProblemKind.FREEZE] at once, before any ANR kill can follow.
 *
 * Runs only between [start] and [stop] (MainActivity's onStart/onStop), so a
 * backgrounded app has no extra thread waking up. Skipped while a debugger is
 * attached, where a breakpoint looks exactly like a freeze.
 */
class MainThreadWatchdog(
    private val onFreeze: (blockedMs: Long, mainStack: List<String>) -> Unit,
    private val onRecovered: (totalBlockedMs: Long) -> Unit,
    private val thresholdMs: Long = FREEZE_THRESHOLD_MS,
    private val checkEveryMs: Long = 250,
    private val pingEveryMs: Long = 1_000,
) {
    @Volatile private var thread: Thread? = null

    fun start() {
        if (thread != null) return
        val t = Thread(::loop, "domovoi-watchdog").apply { isDaemon = true }
        // Published before it runs: the loop exits as soon as it is not
        // the current watchdog thread.
        thread = t
        t.start()
    }

    fun stop() {
        thread?.interrupt()
        thread = null
    }

    private class Ping : Runnable {
        @Volatile var ranAt = -1L
        override fun run() { ranAt = SystemClock.uptimeMillis() }
    }

    private fun loop() {
        val me = Thread.currentThread()
        val main = Looper.getMainLooper()
        val handler = Handler(main)
        val detector = FreezeDetector(thresholdMs)
        try {
            while (thread === me) {
                val ping = Ping()
                val postedAt = SystemClock.uptimeMillis()
                handler.post(ping)
                while (thread === me) {
                    Thread.sleep(checkEveryMs)
                    val ranAt = ping.ranAt.takeIf { it >= 0 }
                    if (Debug.isDebuggerConnected()) {
                        if (ranAt != null) break else continue
                    }
                    when (val step = detector.observe(postedAt, ranAt, SystemClock.uptimeMillis())) {
                        is FreezeDetector.Step.Frozen ->
                            onFreeze(step.blockedMs, main.thread.stackTrace.map { it.toString() })
                        is FreezeDetector.Step.Recovered -> onRecovered(step.blockedMs)
                        FreezeDetector.Step.Ok -> Unit
                    }
                    if (ranAt != null) break
                }
                Thread.sleep(pingEveryMs)
            }
        } catch (_: InterruptedException) {
            // stop()
        } catch (_: Throwable) {
            // A watchdog must never be the thing that crashes the app.
        }
    }

    companion object {
        /** Long enough that a slow first frame of a debug build is not
         *  reported; short of the 5 s input timeout behind an ANR kill. */
        const val FREEZE_THRESHOLD_MS = 3_000L
    }
}

/**
 * The watchdog's decision, kept free of Android so it is unit-tested: given
 * when the current ping was posted and when (if yet) the main thread ran it,
 * report a freeze once per episode and its end.
 */
class FreezeDetector(private val thresholdMs: Long) {
    sealed interface Step {
        data object Ok : Step
        data class Frozen(val blockedMs: Long) : Step
        data class Recovered(val blockedMs: Long) : Step
    }

    private var reported = false

    fun observe(postedAtMs: Long, ranAtMs: Long?, nowMs: Long): Step {
        if (ranAtMs != null) {
            if (!reported) return Step.Ok
            reported = false
            return Step.Recovered(ranAtMs - postedAtMs)
        }
        val blocked = nowMs - postedAtMs
        if (!reported && blocked >= thresholdMs) {
            reported = true
            return Step.Frozen(blocked)
        }
        return Step.Ok
    }
}
