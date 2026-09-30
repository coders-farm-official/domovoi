package com.domovoi.app.alerts

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.domovoi.app.DomovoiApplication
import kotlinx.coroutines.launch

/**
 * Boot and app update clear every alarm; this re-arms the mirror's future
 * ones and restarts the background sync's chain, its first tick within a
 * couple of minutes (TimerSync.onBoot). Not exported: BOOT_COMPLETED and
 * MY_PACKAGE_REPLACED are protected system broadcasts, which reach a
 * non-exported receiver.
 */
class TimerBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED && action != Intent.ACTION_MY_PACKAGE_REPLACED) return
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
