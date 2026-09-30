package com.domovoi.app.alerts

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.domovoi.app.MainActivity
import com.domovoi.app.R
import com.domovoi.app.data.Prefs
import com.domovoi.app.data.ServerCredentials
import com.domovoi.app.net.isSharedScreen

/** Where a notification tap asks MainActivity to go ("home"). */
const val EXTRA_ROUTE = "com.domovoi.app.extra.ROUTE"

/**
 * The notification side of timer alerts: the `timer_fires` channel, and the
 * one notification per timer both paths post into (tag
 * `timer_fire:<serverKey>`, id = the timer id), so the live push and the
 * local alarm land in the same slot.
 *
 * Privacy: the channel and every notification are VISIBILITY_PRIVATE with a
 * public version that says only the kind and the room ("Reminder · garage").
 * That is all a locked phone shows. On a shared screen (the kitchen tablet)
 * the private version is the public one too: no words even unlocked.
 */
class TimerNotifier(context: Context, private val prefs: Prefs) : AlertSink {
    private val ctx = context.applicationContext

    companion object {
        const val CHANNEL_ID = "timer_fires"
        const val CHANNEL_NAME = "Timers and reminders"
        const val CHANNEL_DESCRIPTION = "A timer or reminder going off anywhere in the house"
        const val GROUP = "timer_fires"
        private const val TAG = "TimerNotifier"

        /** Called from DomovoiApplication.onCreate (minSdk 26: channels always exist).
         *  Creating an existing channel only refreshes its name and description;
         *  the user's own choices for it stay. */
        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            val channel = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_HIGH).apply {
                description = CHANNEL_DESCRIPTION
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
                enableVibration(true)
                // Default sound: the channel's own, left as the system sets it.
            }
            nm.createNotificationChannel(channel)
        }

        /** Notifications are on for the app (POST_NOTIFICATIONS on 33+) and
         *  the user hasn't silenced this channel. */
        fun canPost(context: Context): Boolean {
            val compat = NotificationManagerCompat.from(context)
            if (!compat.areNotificationsEnabled()) return false
            val channel = context.getSystemService(NotificationManager::class.java)
                ?.getNotificationChannel(CHANNEL_ID)
            return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
        }

        /** Tapping an alert opens the app on Home. */
        fun homeIntent(context: Context, requestCode: Int): PendingIntent = PendingIntent.getActivity(
            context,
            requestCode,
            Intent(context, MainActivity::class.java)
                .putExtra(EXTRA_ROUTE, "home")
                .addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP,
                ),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    override fun canPost(): Boolean = canPost(ctx)

    override fun shared(): Boolean {
        val url = prefs.serverUrl.value
        return isSharedScreen(ServerCredentials.sharedAnswerFor(prefs.sharedScreens.value, url), prefs.isPaired())
    }

    private fun base(content: AlertContent): NotificationCompat.Builder =
        NotificationCompat.Builder(ctx, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_timer)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setGroup(GROUP)
            .apply {
                content.whenMs?.let { setWhen(it) }
                setShowWhen(content.whenMs != null)
            }

    @SuppressLint("MissingPermission") // canPost() is checked first; a revoke in between is caught
    override fun post(serverKey: String, content: AlertContent, silent: Boolean) {
        if (!canPost()) return
        val public = base(content).setContentTitle(content.publicTitle).build()
        val builder = base(content)
            .setContentTitle(content.title)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setSilent(silent)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setContentIntent(homeIntent(ctx, content.timerId.toInt()))
        content.text?.let { builder.setContentText(it) }
        content.subText?.let { builder.setSubText(it) }
        try {
            NotificationManagerCompat.from(ctx).notify(alertTag(serverKey), content.timerId.toInt(), builder.build())
        } catch (e: SecurityException) {
            Log.i(TAG, "timer alert not posted: ${e.message}")
        }
    }

    override fun isActive(serverKey: String, timerId: Long): Boolean {
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return false
        val tag = alertTag(serverKey)
        return runCatching {
            nm.activeNotifications.any { it.tag == tag && it.id == timerId.toInt() }
        }.getOrDefault(false)
    }
}
