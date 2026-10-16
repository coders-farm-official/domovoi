package com.domovoi.app.alerts

import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings

/**
 * Exact alarms, and the person's grant for them.
 *
 * A mirrored timer rings on time only as an exact alarm (an inexact one may
 * wait out Doze for many minutes), and the background sync's tick gets the
 * network in Doze only as one (TimerSync.kt). From API 31 an app may arm
 * exact alarms only with one of two permissions:
 *
 *  - USE_EXACT_ALARM, granted at install. Google Play allows it only in apps
 *    whose core function is an alarm clock, a timer or a calendar; Domovoi's
 *    timers are one feature of a home assistant, so the app does not ask for
 *    it (android/play/DECLARATIONS.md, "Exact alarms").
 *  - SCHEDULE_EXACT_ALARM, which is what the app declares: the person's own
 *    "Alarms & reminders" grant in the system settings. Android 12 and 13
 *    give it at install; Android 14 and later leave it off for a new
 *    install, and the person can take it back at any time.
 *
 * Without the grant nothing breaks: every timer alarm is armed inexact
 * (useExact) and the tick runs as a plain alarm (ChainMode.PLAIN), so a
 * timer can ring some minutes late. Home's timers card says so once and
 * offers the grant ([timerAlertHint]); the system's answer comes back as
 * [ACTION_GRANT_CHANGED] (TimerBootReceiver re-arms everything exact) or,
 * when the person returns to the app, through MainActivity.onResume
 * (TimerAlerts.onAppResumed). Taking the grant away stops the app, and its
 * next start arms inexact.
 */
object ExactAlarms {
    /** AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED (API 31):
     *  sent to the app when the grant is given. */
    const val ACTION_GRANT_CHANGED = "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED"

    /** Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM (API 31): the system's
     *  "Alarms & reminders" page for one app. */
    const val ACTION_REQUEST_GRANT = "android.settings.REQUEST_SCHEDULE_EXACT_ALARM"

    /** Below API 31 every app may arm exact alarms; from 31 only with the grant. */
    fun needsGrant(sdkInt: Int): Boolean = sdkInt >= 31 /* S */

    /** Exact alarms are allowed on this phone right now. */
    fun allowed(context: Context): Boolean {
        if (!needsGrant(Build.VERSION.SDK_INT)) return true
        val am = context.getSystemService(AlarmManager::class.java) ?: return false
        return am.canScheduleExactAlarms()
    }

    /** The intent for this app's "Alarms & reminders" page. */
    fun grantIntent(packageName: String): Intent =
        Intent(ACTION_REQUEST_GRANT, Uri.parse("package:$packageName"))

    /**
     * Opens the "Alarms & reminders" page (falls back to the app's own
     * settings page on a phone that lacks it); false when neither opens.
     */
    fun openGrant(context: Context): Boolean {
        if (!needsGrant(Build.VERSION.SDK_INT)) return false
        val tries = listOf(
            grantIntent(context.packageName),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}")),
        )
        return tries.any { runCatching { context.startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }.isSuccess }
    }
}

/** What Home's timers card says about this phone's alerts, at most one line. */
enum class TimerAlertHint {
    /** Nothing to say: alerts post and ring on time. */
    NONE,

    /** Notifications are off for the app: nothing rings here at all. */
    NOTIFICATIONS_OFF,

    /** Notifications are on but exact alarms are not allowed: a timer may
     *  ring some minutes late. */
    MAY_RING_LATE,
}

/**
 * The timers card's line: notifications off outranks a late ring (nothing
 * rings at all), and each one is said until its own "not now".
 */
fun timerAlertHint(
    canPost: Boolean,
    canExact: Boolean,
    notificationsHintDismissed: Boolean,
    exactHintDismissed: Boolean,
): TimerAlertHint = when {
    !canPost -> if (notificationsHintDismissed) TimerAlertHint.NONE else TimerAlertHint.NOTIFICATIONS_OFF
    !canExact -> if (exactHintDismissed) TimerAlertHint.NONE else TimerAlertHint.MAY_RING_LATE
    else -> TimerAlertHint.NONE
}
