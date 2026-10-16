package com.domovoi.app.alerts

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.domovoi.app.DomovoiApplication
import com.domovoi.app.net.HouseholdLock
import kotlinx.coroutines.launch

/**
 * Boot and app update clear every alarm; this re-arms the mirror's future
 * ones and restarts the background sync's chain, its first tick within a
 * couple of minutes (TimerSync.onBoot). The "Alarms & reminders" grant
 * being given (API 31+, [ExactAlarms]) gets the same re-arm, so the alarms
 * armed inexact without it become exact. Not exported: BOOT_COMPLETED,
 * MY_PACKAGE_REPLACED and the grant's broadcast come from the system, which
 * reaches a non-exported receiver.
 */
class TimerBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in REARM_ACTIONS) return
        // Room mode: nothing household is re-armed on a room phone.
        if (HouseholdLock.roomModeActive) return
        val app = context.applicationContext as? DomovoiApplication ?: return
        val pending = goAsync()
        receiverScope.launch {
            try {
                app.container.alerts.sync.onBoot()
            } catch (e: Exception) {
                Log.w("TimerBootReceiver", "re-arming timer alarms failed: ${e.message}")
            } finally {
                pending.finish()
            }
        }
    }
}

/** What [TimerBootReceiver] re-arms on (and its manifest filter lists). */
internal val REARM_ACTIONS = setOf(
    Intent.ACTION_BOOT_COMPLETED,
    Intent.ACTION_MY_PACKAGE_REPLACED,
    ExactAlarms.ACTION_GRANT_CHANGED,
)
