package com.domovoi.app.roommode

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.domovoi.app.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * Room mode, process-wide: the running [RoomEngine] (when the service has
 * one) and its status for the room screen.
 */
object RoomMode {
    private val _engine = MutableStateFlow<RoomEngine?>(null)
    val engine: StateFlow<RoomEngine?> = _engine

    internal fun attach(e: RoomEngine?) {
        _engine.value = e
    }

    /** Starts the room's service. Only from a screen the person is looking
     *  at: Android lets a microphone service start only then. */
    fun startService(context: Context) {
        val store = RoomStore(context)
        store.setRunning(true)
        ContextCompat.startForegroundService(context, Intent(context, RoomService::class.java))
    }

    /** Stops listening (the notification's Stop): the room screen stays,
     *  locked, with a Resume button. */
    fun stopService(context: Context) {
        RoomStore(context).setRunning(false)
        context.stopService(Intent(context, RoomService::class.java))
    }

    /** Leaves room mode altogether (after the device unlock): the normal
     *  app opens again. The room's key and enrolment are kept, so coming
     *  back is "Start room mode" again; [forget] removes them. */
    fun exit(context: Context) {
        stopService(context)
        RoomStore(context).setActive(false)
        RoomNotifications.cancelResume(context)
    }

    fun forget(context: Context) {
        exit(context)
        RoomStore(context).forget()
    }
}

/**
 * The foreground service that IS the room while it runs: type microphone
 * (it listens with the screen off) and media playback (it speaks and plays
 * the room's music), a Wi-Fi lock and a partial wake lock so the socket and
 * the mic survive the screen going off. Android shows its notification and
 * the green mic dot for as long as it runs; that is the point.
 *
 * Started only from the room screen (visible), which Android requires of a
 * microphone service. After a reboot, or when Android killed the process,
 * it does not start itself: [RoomNotifications.showResume] asks for a tap.
 */
class RoomService : Service() {
    private var engine: RoomEngine? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var statusJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            RoomMode.stopService(this)
            return START_NOT_STICKY
        }
        val store = RoomStore(this)
        val decision = RoomLockRules.start(
            stickyRestart = intent == null,
            active = store.active,
            enrolled = store.enrolment != null,
            running = store.running,
            micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED,
            holdsHouseholdToken = RoomDevice.holdsHouseholdToken(this),
            deviceSecure = RoomDevice.deviceSecure(this),
        )
        if (decision != RoomLockRules.Start.RUN) {
            // A sticky restart, no mic permission, a household phone with no
            // screen lock, or nothing to run: never take the mic from the
            // background; the room screen (or a tap) takes it from here.
            if (decision == RoomLockRules.Start.ASK_FOR_TAP) RoomNotifications.showResume(this)
            stopSelf()
            return START_NOT_STICKY
        }
        RoomNotifications.createChannels(this)
        RoomNotifications.cancelResume(this)
        val label = store.enrolment?.roomLabel ?: "room"
        val types = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
        } else {
            0
        }
        ServiceCompat.startForeground(this, RoomNotifications.ONGOING_ID, RoomNotifications.ongoing(this, label, "starting"), types)
        if (engine == null) {
            val e = RoomEngine(applicationContext, store)
            if (!e.start()) {
                RoomNotifications.update(this, label, e.status.value.link.let { (it as? RoomEngine.Link.Offline)?.reason ?: "could not start" })
            }
            engine = e
            RoomMode.attach(e)
            acquireLocks()
            statusJob = scope.launch {
                e.status.map { RoomStatusText.notificationLine(it) }.distinctUntilChanged().collect {
                    RoomNotifications.update(this@RoomService, label, it)
                }
            }
        }
        return START_STICKY
    }

    @Suppress("DEPRECATION")
    private fun acquireLocks() {
        val wm = applicationContext.getSystemService(WifiManager::class.java)
        wifiLock = runCatching {
            wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "domovoi:room").apply { setReferenceCounted(false); acquire() }
        }.getOrNull()
        val pm = getSystemService(PowerManager::class.java)
        wakeLock = runCatching {
            pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "domovoi:room").apply { setReferenceCounted(false); acquire() }
        }.getOrNull()
    }

    override fun onDestroy() {
        statusJob?.cancel()
        scope.cancel()
        engine?.stop()
        engine = null
        RoomMode.attach(null)
        runCatching { wifiLock?.release() }
        runCatching { wakeLock?.release() }
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.domovoi.app.room.STOP"
    }
}

/** The room's two notifications: the service's own (ongoing, with Stop),
 *  and the after-reboot "tap to resume". */
object RoomNotifications {
    const val CHANNEL = "room_mode"
    const val ONGOING_ID = 4_101
    const val RESUME_ID = 4_102

    fun createChannels(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Room mode", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while this device is a Domovoi room and listening for the wake word"
                setShowBadge(false)
            },
        )
    }

    private fun openRoom(context: Context): PendingIntent = PendingIntent.getActivity(
        context, 0,
        Intent(context, RoomModeActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun ongoing(context: Context, label: String, line: String): Notification {
        val stop = PendingIntent.getService(
            context, 1, Intent(context, RoomService::class.java).setAction(RoomService.ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val action = NotificationCompat.Action.Builder(0, "Stop listening", stop)
            .setAuthenticationRequired(true)
            .build()
        return NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_room)
            .setContentTitle("Domovoi Room · $label")
            .setContentText(line)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(openRoom(context))
            .addAction(action)
            .build()
    }

    fun update(context: Context, label: String, line: String) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        runCatching { nm.notify(ONGOING_ID, ongoing(context, label, line)) }
    }

    /** After a reboot (or a killed process): one tap brings the room back. */
    fun showResume(context: Context) {
        createChannels(context)
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val label = RoomStore(context).enrolment?.roomLabel ?: "room"
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_room)
            .setContentTitle("Domovoi Room · $label is not listening")
            .setContentText("Tap to resume room mode")
            .setAutoCancel(true)
            .setContentIntent(openRoom(context))
            .build()
        runCatching { nm.notify(RESUME_ID, n) }
    }

    fun cancelResume(context: Context) {
        context.getSystemService(NotificationManager::class.java)?.cancel(RESUME_ID)
    }
}

/** After a reboot: Android lets no app start a microphone service from the
 *  boot broadcast, so a room that was running asks for one tap. */
class RoomBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val store = RoomStore(context)
        if (store.active && store.running) RoomNotifications.showResume(context)
    }
}
