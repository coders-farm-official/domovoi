package com.domovoi.app.roommode

import android.app.KeyguardManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.wifi.WifiManager
import android.os.BatteryManager
import com.domovoi.app.DomovoiApplication
import java.io.File

/** The phone facts a room's health sample carries; each null when the
 *  phone will not say. No permission beyond what the app holds (the Wi-Fi
 *  name needs location access, so it is never read). */
object RoomDevice {
    /** [plugged]: which charger, "ac" / "usb" / "wireless" / "dock" (null
     *  when on battery or unknown). */
    data class Battery(
        val percent: Int?,
        val charging: Boolean?,
        val onPower: Boolean?,
        val tempC: Double?,
        val plugged: String? = null,
    ) {
        /** On its charger (charging, or held full on it); null when unknown. */
        val onCharger: Boolean?
            get() = if (charging == true || onPower == true) true else if (charging == null && onPower == null) null else false
    }
    data class Wifi(val linkMbps: Int?, val rssi: Int?)

    fun battery(context: Context): Battery {
        val sticky = runCatching {
            context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        }.getOrNull()
        val bm = context.getSystemService(BatteryManager::class.java)
        val capacity = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        return battery(sticky, capacity)
    }

    /** From an `ACTION_BATTERY_CHANGED` intent (the sticky one, or a
     *  broadcast), with BatteryManager's capacity when the caller has it. */
    fun battery(intent: Intent?, capacity: Int? = null): Battery = batteryFrom(
        capacity = capacity,
        level = intent?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1,
        scale = intent?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1,
        status = intent?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1,
        plugged = intent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, -1) ?: -1,
        tempTenths = intent?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE) ?: Int.MIN_VALUE,
    )

    /** The broadcast's extras -> [Battery]; pure, for the JVM tests. -1 (or
     *  [Int.MIN_VALUE] for the temperature) is "not said". */
    fun batteryFrom(capacity: Int?, level: Int, scale: Int, status: Int, plugged: Int, tempTenths: Int): Battery {
        // The broadcast's own level first: it and its status/plugged are one
        // reading (a phone may keep a capacity property that lags it).
        val pct = (if (level >= 0 && scale > 0) (level * 100 / scale).coerceIn(0, 100) else null)
            ?: capacity?.takeIf { it in 0..100 }
        val onPower = if (plugged < 0) null else plugged != 0
        // Charging only while the level goes up; a full battery on its
        // charger is on_power, not charging (the core's battery shape).
        // Unplugged is never charging, whatever the status still says.
        val charging = when {
            onPower == false -> false
            status == BATTERY_STATUS_CHARGING -> true
            status == -1 -> null
            else -> false
        }
        val temp = tempTenths.takeIf { it != Int.MIN_VALUE }?.let { it / 10.0 }
        return Battery(pct, charging, onPower, temp, pluggedName(plugged))
    }

    /** Android's EXTRA_PLUGGED bits -> the core's `plugged` word. */
    fun pluggedName(plugged: Int): String? = when {
        plugged <= 0 -> null
        plugged and PLUGGED_AC != 0 -> "ac"
        plugged and PLUGGED_USB != 0 -> "usb"
        plugged and PLUGGED_WIRELESS != 0 -> "wireless"
        plugged and PLUGGED_DOCK != 0 -> "dock"
        else -> null
    }

    // BatteryManager's constants, as numbers: the JVM tests have no Android
    // framework (the values are fixed by the platform's API).
    private const val BATTERY_STATUS_CHARGING = 2
    private const val PLUGGED_AC = 1
    private const val PLUGGED_USB = 2
    private const val PLUGGED_WIRELESS = 4
    private const val PLUGGED_DOCK = 8

    @Suppress("DEPRECATION")
    fun wifi(context: Context): Wifi {
        val wm = context.applicationContext.getSystemService(WifiManager::class.java) ?: return Wifi(null, null)
        val info = runCatching { wm.connectionInfo }.getOrNull() ?: return Wifi(null, null)
        val speed = info.linkSpeed.takeIf { it > 0 }
        val rssi = info.rssi.takeIf { it in -120..-1 }
        return Wifi(speed, rssi)
    }

    /** A household token for any server is on this phone (the app's vault). */
    fun holdsHouseholdToken(context: Context): Boolean =
        (context.applicationContext as? DomovoiApplication)?.container?.prefs?.holdsHouseholdToken() ?: false

    /** The phone has a PIN, pattern, password (or biometrics over one). */
    fun deviceSecure(context: Context): Boolean =
        context.getSystemService(KeyguardManager::class.java)?.isDeviceSecure ?: false

    /** VmRSS of this process, in kB. */
    fun rssKb(): Long? = status("VmRSS:")?.split(Regex("\\s+"))?.firstOrNull()?.toLongOrNull()

    fun threads(): Int? = status("Threads:")?.trim()?.toIntOrNull()

    private fun status(key: String): String? = runCatching {
        File("/proc/self/status").readLines().firstOrNull { it.startsWith(key) }?.removePrefix(key)?.trim()
    }.getOrNull()
}
