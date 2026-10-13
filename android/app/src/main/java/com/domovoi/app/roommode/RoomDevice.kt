package com.domovoi.app.roommode

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.WifiManager
import android.os.BatteryManager
import java.io.File

/** The phone facts a room's health sample carries; each null when the
 *  phone will not say. No permission beyond what the app holds (the Wi-Fi
 *  name needs location access, so it is never read). */
object RoomDevice {
    data class Battery(val percent: Int?, val charging: Boolean?, val onPower: Boolean?, val tempC: Double?)
    data class Wifi(val linkMbps: Int?, val rssi: Int?)

    fun battery(context: Context): Battery {
        val sticky = runCatching {
            context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        }.getOrNull()
        val bm = context.getSystemService(BatteryManager::class.java)
        val pct = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)?.takeIf { it in 0..100 }
            ?: sticky?.let { i ->
                val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
                val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
                if (level >= 0 && scale > 0) level * 100 / scale else null
            }
        val status = sticky?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = when (status) {
            BatteryManager.BATTERY_STATUS_CHARGING, BatteryManager.BATTERY_STATUS_FULL -> true
            -1 -> null
            else -> false
        }
        val plugged = sticky?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1) ?: -1
        val onPower = if (plugged < 0) null else plugged != 0
        val temp = sticky?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)
            ?.takeIf { it != Int.MIN_VALUE }?.let { it / 10.0 }
        return Battery(pct, charging, onPower, temp)
    }

    @Suppress("DEPRECATION")
    fun wifi(context: Context): Wifi {
        val wm = context.applicationContext.getSystemService(WifiManager::class.java) ?: return Wifi(null, null)
        val info = runCatching { wm.connectionInfo }.getOrNull() ?: return Wifi(null, null)
        val speed = info.linkSpeed.takeIf { it > 0 }
        val rssi = info.rssi.takeIf { it in -120..-1 }
        return Wifi(speed, rssi)
    }

    /** VmRSS of this process, in kB. */
    fun rssKb(): Long? = status("VmRSS:")?.split(Regex("\\s+"))?.firstOrNull()?.toLongOrNull()

    fun threads(): Int? = status("Threads:")?.trim()?.toIntOrNull()

    private fun status(key: String): String? = runCatching {
        File("/proc/self/status").readLines().firstOrNull { it.startsWith(key) }?.removePrefix(key)?.trim()
    }.getOrNull()
}
