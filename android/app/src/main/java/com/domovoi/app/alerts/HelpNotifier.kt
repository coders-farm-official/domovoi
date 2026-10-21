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

/** The route a help notification's tap asks MainActivity for. */
const val CHAT_ROUTE = "chat"

/** The server chat thread a help notification opens, and its title. */
const val EXTRA_CHAT_THREAD = "com.domovoi.app.extra.CHAT_THREAD"
const val EXTRA_CHAT_TITLE = "com.domovoi.app.extra.CHAT_TITLE"

// EXTRA_SERVER_KEY (TimerAlarmMirror.kt) carries the server the thread is
// on: a tap after a switch to another server opens Chat, not a thread id
// that means nothing there.

/** The notification tag help steps post under (the id is the delivery's). */
fun helpTag(serverKey: String): String = "help_delivery:$serverKey"

/**
 * The notification side of voice help: a "Help" channel of its own, one
 * notification per delivery. Privacy as for the other alerts: the lock
 * screen's public version says only "Domovoi sent you help steps".
 */
class HelpNotifier(context: Context, private val prefs: Prefs) : HelpSink {
    private val ctx = context.applicationContext

    companion object {
        const val CHANNEL_ID = "help"
        const val CHANNEL_NAME = "Help"
        const val CHANNEL_DESCRIPTION = "Steps Domovoi sends to your phone when you ask a room how to do something"
        const val ACTION_OPEN = "com.domovoi.app.action.OPEN_HELP"
        private const val TAG = "HelpNotifier"

        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            val channel = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = CHANNEL_DESCRIPTION
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            }
            nm.createNotificationChannel(channel)
        }

        fun canPost(context: Context): Boolean {
            val compat = NotificationManagerCompat.from(context)
            if (!compat.areNotificationsEnabled()) return false
            val channel = context.getSystemService(NotificationManager::class.java)
                ?.getNotificationChannel(CHANNEL_ID)
            return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
        }

        fun openIntent(context: Context, serverKey: String, row: HelpDelivery): PendingIntent = PendingIntent.getActivity(
            context,
            row.id.toInt(),
            Intent(context, MainActivity::class.java)
                .setAction(ACTION_OPEN)
                .putExtra(EXTRA_ROUTE, CHAT_ROUTE)
                .putExtra(EXTRA_CHAT_THREAD, row.threadId)
                .putExtra(EXTRA_CHAT_TITLE, helpTitle(row))
                .putExtra(EXTRA_SERVER_KEY, serverKey)
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

    @SuppressLint("MissingPermission") // canPost() is checked first; a revoke in between is caught
    override fun post(serverKey: String, row: HelpDelivery) {
        if (!canPost()) return
        fun base() = NotificationCompat.Builder(ctx, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_timer)
            .setCategory(NotificationCompat.CATEGORY_RECOMMENDATION)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
        val public = base().setContentTitle("Domovoi sent you help steps").build()
        val text = "Tap to open the steps in Chat."
        val notification = base()
            .setContentTitle(helpTitle(row))
            .setContentText(text)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setContentIntent(openIntent(ctx, serverKey, row))
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify(helpTag(serverKey), row.id.toInt(), notification)
        } catch (e: SecurityException) {
            Log.i(TAG, "help steps not posted: ${e.message}")
        }
    }
}
