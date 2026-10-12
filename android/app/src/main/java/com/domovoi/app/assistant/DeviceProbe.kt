package com.domovoi.app.assistant

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ApplicationInfo
import android.net.ConnectivityManager
import android.net.TrafficStats
import android.os.BatteryManager
import android.os.Build
import android.os.PowerManager
import android.os.Process
import java.io.File

/** What the benchmark reads from the phone itself: the device, memory,
 *  temperature, and whether (and how much) the app used the network. */
class DeviceProbe(private val context: Context) {

    private val activity get() = context.getSystemService(ActivityManager::class.java)

    fun facts(): DeviceFacts {
        val info = ActivityManager.MemoryInfo().also { activity.getMemoryInfo(it) }
        @Suppress("DEPRECATION")
        val pkg = runCatching { context.packageManager.getPackageInfo(context.packageName, 0) }.getOrNull()
        val debug = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
        return DeviceFacts(
            manufacturer = Build.MANUFACTURER,
            model = Build.MODEL,
            soc = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                listOf(Build.SOC_MANUFACTURER, Build.SOC_MODEL).filter { it.isNotBlank() && it != Build.UNKNOWN }
                    .joinToString(" ").ifBlank { null }
            } else {
                Build.HARDWARE
            },
            android = Build.VERSION.RELEASE,
            api = Build.VERSION.SDK_INT,
            totalRamBytes = info.totalMem,
            lowRamDevice = activity.isLowRamDevice,
            abi = Build.SUPPORTED_ABIS.firstOrNull() ?: "?",
            app = "${pkg?.versionName ?: "?"}${if (debug) " (debug build)" else ""}",
        )
    }

    fun memory(label: String): MemoryReading {
        val (peak, now) = runCatching { parseProcStatus(File("/proc/self/status").readText()) }
            .getOrDefault(null to null)
        val info = ActivityManager.MemoryInfo().also { activity.getMemoryInfo(it) }
        return MemoryReading(label, peak, now, info.availMem)
    }

    fun reading(): PhoneReading = PhoneReading(batteryTempC(), thermalStatus(), online())

    fun traffic(label: String): TrafficReading {
        val uid = Process.myUid()
        return TrafficReading(label, TrafficStats.getUidRxBytes(uid), TrafficStats.getUidTxBytes(uid), online())
    }

    fun online(): Boolean =
        context.getSystemService(ConnectivityManager::class.java)?.activeNetwork != null

    private fun batteryTempC(): Double? {
        val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return null
        val tenths = battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE)
        return if (tenths == Int.MIN_VALUE) null else tenths / 10.0
    }

    private fun thermalStatus(): Int? =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            context.getSystemService(PowerManager::class.java)?.currentThermalStatus
        } else {
            null
        }
}
