package com.domovoi.app.assistant

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build

/**
 * How the app's last run ended, from Android's own record
 * (`ActivityManager.getHistoricalProcessExitReasons`, Android 11+): the
 * photo crash guard (VisionCheck) blames the encoder only for a real crash.
 * Older Android has no record: [ExitKind.UNKNOWN].
 */
class AndroidExitReasons(context: Context) : ExitReasons {
    private val app = context.applicationContext

    override fun since(ms: Long): ExitKind {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return ExitKind.UNKNOWN
        val am = app.getSystemService(ActivityManager::class.java) ?: return ExitKind.UNKNOWN
        val exits = runCatching { am.getHistoricalProcessExitReasons(app.packageName, 0, 16) }.getOrNull()
            ?: return ExitKind.UNKNOWN
        // The main process's first death at or after the mark ended that run.
        val first = exits.filter { it.timestamp >= ms && it.processName == app.packageName }.minByOrNull { it.timestamp }
            ?: return ExitKind.UNKNOWN
        return exitKind(first.reason)
    }
}

/** An ApplicationExitInfo reason as the crash guard reads it. */
fun exitKind(reason: Int): ExitKind = when (reason) {
    ApplicationExitInfo.REASON_CRASH,
    ApplicationExitInfo.REASON_CRASH_NATIVE,
    ApplicationExitInfo.REASON_ANR,
    ApplicationExitInfo.REASON_INITIALIZATION_FAILURE,
    -> ExitKind.CRASH
    ApplicationExitInfo.REASON_UNKNOWN -> ExitKind.UNKNOWN
    // Memory kills, swipes, force-stops, updates, permission changes...
    else -> ExitKind.NOT_A_CRASH
}
