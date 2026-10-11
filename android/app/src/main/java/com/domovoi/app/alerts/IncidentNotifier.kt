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

/** The route a container incident's tap asks MainActivity for. */
const val CONTAINERS_ROUTE = "containers"

/**
 * The notification side of the container guard: the `container_incidents`
 * channel, apart from the timers' so the two can be silenced separately,
 * and one notification per incident (tag `container_incident:<serverKey>`,
 * id = the incident id), posted once ([planNotifications]). Tapping one
 * opens the Containers screen.
 *
 * Privacy, as for the timer alerts (TimerNotifier): VISIBILITY_PRIVATE with
 * a public version that says only "Domovoi needs an admin's attention",
 * which Android shows on the lock screen when the phone hides sensitive
 * content. On a shared screen (the kitchen tablet) the private version is
 * the public one too.
 */
class IncidentNotifier(context: Context, private val prefs: Prefs) : IncidentSink {
    private val ctx = context.applicationContext

    companion object {
        const val CHANNEL_ID = "container_incidents"
        const val CHANNEL_NAME = "Container problems"
        const val CHANNEL_DESCRIPTION = "A Domovoi container failed, or shows signs of tampering"
        const val GROUP = "container_incidents"

        /** Its own action, so a tap's PendingIntent never matches a timer
         *  alert's (same activity, and request codes that can coincide:
         *  extras are not part of a PendingIntent's identity). */
        const val ACTION_OPEN = "com.domovoi.app.action.OPEN_CONTAINERS"
        private const val TAG = "IncidentNotifier"

        /** Called from DomovoiApplication.onCreate, beside the timers' channel.
         *  Creating an existing channel only refreshes its name and
         *  description; the user's own choices for it stay. */
        fun createChannel(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return
            val channel = NotificationChannel(CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_HIGH).apply {
                description = CHANNEL_DESCRIPTION
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
                enableVibration(true)
            }
            nm.createNotificationChannel(channel)
        }

        /** Notifications are on for the app and the user hasn't silenced
         *  this channel. */
        fun canPost(context: Context): Boolean {
            val compat = NotificationManagerCompat.from(context)
            if (!compat.areNotificationsEnabled()) return false
            val channel = context.getSystemService(NotificationManager::class.java)
                ?.getNotificationChannel(CHANNEL_ID)
            return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
        }

        /** Tapping an incident opens the Containers screen. */
        fun detailsIntent(context: Context, requestCode: Int): PendingIntent = PendingIntent.getActivity(
            context,
            requestCode,
            Intent(context, MainActivity::class.java)
                .setAction(ACTION_OPEN)
                .putExtra(EXTRA_ROUTE, CONTAINERS_ROUTE)
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

    private fun base(content: IncidentContent): NotificationCompat.Builder =
        NotificationCompat.Builder(ctx, CHANNEL_ID)
            // The app's one status-bar glyph (the timers' bell); no new artwork.
            .setSmallIcon(R.drawable.ic_stat_timer)
            .setCategory(NotificationCompat.CATEGORY_ERROR)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setGroup(GROUP)
            .apply {
                content.whenMs?.let { setWhen(it) }
                setShowWhen(content.whenMs != null)
            }

    @SuppressLint("MissingPermission") // canPost() is checked first; a revoke in between is caught
    override fun post(serverKey: String, content: IncidentContent) {
        if (!canPost()) return
        val public = base(content).setContentTitle(content.publicTitle).build()
        val builder = base(content)
            .setContentTitle(content.title)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .setContentIntent(detailsIntent(ctx, content.incidentId.toInt()))
        content.text?.let {
            builder.setContentText(it)
            builder.setStyle(NotificationCompat.BigTextStyle().bigText(it))
        }
        try {
            NotificationManagerCompat.from(ctx).notify(incidentTag(serverKey), content.incidentId.toInt(), builder.build())
        } catch (e: SecurityException) {
            Log.i(TAG, "container incident not posted: ${e.message}")
        }
    }
}
