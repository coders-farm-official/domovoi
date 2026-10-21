package com.domovoi.app.assistant

import android.Manifest
import android.app.ActivityManager
import android.app.AlarmManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.os.SystemClock
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.provider.Settings
import androidx.core.content.ContextCompat
import java.time.Instant
import java.time.ZoneId
import java.time.ZoneOffset

/**
 * The phone's own readings for a phone chat (PhoneReader), each read when it
 * is asked and only then. None opens a connection or asks a server: the
 * network readings are ConnectivityManager's and WifiManager's view of the
 * phone's own link (NoNetworkTest names exactly which classes may be read
 * here). The location is the last fix Android already has, never a fresh
 * request, never turned into an address (that would go online).
 *
 * Calendar, contacts and location answer only with their permission held;
 * without it they return null and the chat says it can't.
 */
class AndroidPhoneReader(context: Context) : PhoneReader {
    private val ctx = context.applicationContext

    private fun has(permission: String): Boolean =
        ContextCompat.checkSelfPermission(ctx, permission) == PackageManager.PERMISSION_GRANTED

    override fun battery(): BatteryReading? = runCatching {
        val bm = ctx.getSystemService(BatteryManager::class.java) ?: return null
        val sticky = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val percent = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }
            ?: sticky?.let { i ->
                val level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
                val scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
                if (level >= 0 && scale > 0) level * 100 / scale else null
            } ?: return null
        val status = sticky?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val plugged = (sticky?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        BatteryReading(
            percent = percent,
            charging = bm.isCharging || status == BatteryManager.BATTERY_STATUS_CHARGING,
            full = status == BatteryManager.BATTERY_STATUS_FULL || (plugged && percent >= 100),
        )
    }.getOrNull()

    override fun storage(): StorageReading? = runCatching {
        val fs = StatFs(Environment.getDataDirectory().path)
        StorageReading(fs.availableBytes, fs.totalBytes)
    }.getOrNull()

    override fun network(): NetworkReading? = runCatching {
        val airplane = Settings.Global.getInt(ctx.contentResolver, Settings.Global.AIRPLANE_MODE_ON, 0) != 0
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return null
        val caps = cm.activeNetwork?.let { cm.getNetworkCapabilities(it) }
        val kind = when {
            caps == null -> NetworkKind.NONE
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> NetworkKind.WIFI
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> NetworkKind.MOBILE
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> NetworkKind.ETHERNET
            else -> NetworkKind.OTHER
        }
        NetworkReading(kind, airplane)
    }.getOrNull()

    override fun nextAlarm(): AlarmReading? = runCatching {
        ctx.getSystemService(AlarmManager::class.java)?.nextAlarmClock?.let { AlarmReading(it.triggerTime) }
    }.getOrNull()

    override fun doNotDisturb(): DndMode? = runCatching {
        when (ctx.getSystemService(NotificationManager::class.java)?.currentInterruptionFilter) {
            NotificationManager.INTERRUPTION_FILTER_ALL -> DndMode.OFF
            NotificationManager.INTERRUPTION_FILTER_PRIORITY -> DndMode.PRIORITY
            NotificationManager.INTERRUPTION_FILTER_ALARMS -> DndMode.ALARMS
            NotificationManager.INTERRUPTION_FILTER_NONE -> DndMode.SILENT
            null -> null
            else -> DndMode.UNKNOWN
        }
    }.getOrNull()

    override fun ringer(): RingerMode? = runCatching {
        when (ctx.getSystemService(AudioManager::class.java)?.ringerMode) {
            AudioManager.RINGER_MODE_SILENT -> RingerMode.SILENT
            AudioManager.RINGER_MODE_VIBRATE -> RingerMode.VIBRATE
            AudioManager.RINGER_MODE_NORMAL -> RingerMode.NORMAL
            else -> null
        }
    }.getOrNull()

    override fun device(): DeviceReading? = runCatching {
        val info = ActivityManager.MemoryInfo().also { ctx.getSystemService(ActivityManager::class.java).getMemoryInfo(it) }
        DeviceReading(Build.MANUFACTURER, Build.MODEL, Build.VERSION.RELEASE, Build.VERSION.SDK_INT, info.totalMem)
    }.getOrNull()

    /** Android names the Wi-Fi network only to an app with precise
     *  location; "<unknown ssid>" otherwise. */
    @Suppress("DEPRECATION")
    override fun wifiName(): String? = runCatching {
        if (!has(Manifest.permission.ACCESS_FINE_LOCATION)) return null
        val ssid = ctx.getSystemService(WifiManager::class.java)?.connectionInfo?.ssid ?: return null
        ssid.removeSurrounding("\"").takeIf { it.isNotBlank() && it != WifiManager.UNKNOWN_SSID && it != "<unknown ssid>" }
    }.getOrNull()

    override fun location(): LocationReading? = runCatching {
        if (!has(Manifest.permission.ACCESS_FINE_LOCATION) && !has(Manifest.permission.ACCESS_COARSE_LOCATION)) return null
        val lm = ctx.getSystemService(LocationManager::class.java) ?: return null
        @Suppress("MissingPermission")
        val best = lm.getProviders(true).mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
            .maxByOrNull { it.elapsedRealtimeNanos } ?: return null
        val ageMs = (SystemClock.elapsedRealtimeNanos() - best.elapsedRealtimeNanos) / 1_000_000
        LocationReading(best.latitude, best.longitude, best.accuracy.takeIf { best.hasAccuracy() }, ageMs.takeIf { it >= 0 })
    }.getOrNull()

    override fun headphones(): List<String>? = runCatching {
        val am = ctx.getSystemService(AudioManager::class.java) ?: return null
        val bluetooth = buildSet {
            add(AudioDeviceInfo.TYPE_BLUETOOTH_A2DP)
            add(AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) add(AudioDeviceInfo.TYPE_HEARING_AID)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(AudioDeviceInfo.TYPE_BLE_HEADSET)
                add(AudioDeviceInfo.TYPE_BLE_SPEAKER)
            }
        }
        am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)
            .filter { it.type in bluetooth }
            .map { it.productName?.toString()?.trim().orEmpty().ifBlank { "Bluetooth headphones" } }
            .distinct()
    }.getOrNull()

    override fun calendar(fromMs: Long, toMs: Long): List<CalendarEvent>? = runCatching {
        if (!has(Manifest.permission.READ_CALENDAR)) return null
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon()
            .appendPath(fromMs.toString()).appendPath(toMs.toString()).build()
        val proj = arrayOf(
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.ALL_DAY,
            CalendarContract.Instances.EVENT_LOCATION,
        )
        val out = mutableListOf<CalendarEvent>()
        ctx.contentResolver.query(uri, proj, "${CalendarContract.Instances.VISIBLE} = 1", null,
            "${CalendarContract.Instances.BEGIN} ASC")?.use { c ->
            while (c.moveToNext() && out.size < MAX_EVENTS) {
                val allDay = c.getInt(3) != 0
                // An all-day event's times are UTC midnights: its day on this
                // phone is that date, from local midnight.
                val begin = if (allDay) localMidnight(c.getLong(1)) else c.getLong(1)
                val end = c.getLong(2).let { if (allDay) localMidnight(it) else it }
                if (allDay && end <= fromMs) continue
                out += CalendarEvent(c.getString(0)?.ifBlank { null } ?: "(no title)", begin, end, allDay, c.getString(4)?.ifBlank { null })
            }
        }
        out.sortedBy { it.beginMs }
    }.getOrNull()

    override fun contacts(name: String): List<ContactMatch>? = runCatching {
        if (!has(Manifest.permission.READ_CONTACTS) || name.isBlank()) return null
        val uri = Uri.withAppendedPath(ContactsContract.CommonDataKinds.Phone.CONTENT_FILTER_URI, Uri.encode(name.trim()))
        val proj = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
            ContactsContract.CommonDataKinds.Phone.TYPE,
            ContactsContract.CommonDataKinds.Phone.IS_SUPER_PRIMARY,
        )
        val out = mutableListOf<ContactMatch>()
        ctx.contentResolver.query(uri, proj, null, null, null)?.use { c ->
            while (c.moveToNext() && out.size < MAX_CONTACTS) {
                val type = c.getInt(2)
                val label = when (type) {
                    ContactsContract.CommonDataKinds.Phone.TYPE_MOBILE -> "mobile"
                    ContactsContract.CommonDataKinds.Phone.TYPE_HOME -> "home"
                    ContactsContract.CommonDataKinds.Phone.TYPE_WORK -> "work"
                    else -> null
                }
                out += ContactMatch(c.getString(0) ?: continue, c.getString(1), null, label)
            }
        }
        // A person's mobile first, then the rest as the contacts app has them.
        out.sortedByDescending { it.label == "mobile" }
    }.getOrNull()

    private fun localMidnight(utcMs: Long): Long {
        val date = Instant.ofEpochMilli(utcMs).atOffset(ZoneOffset.UTC).toLocalDate()
        return date.atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()
    }

    private companion object {
        const val MAX_EVENTS = 20
        const val MAX_CONTACTS = 10
    }
}
