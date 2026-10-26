package com.domovoi.app.roommode

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The room phone's `health` frame (sent once a minute when the core's
 * `ready` lists `health`): the Pi's sample where a phone has the fact, and
 * a phone's own — the battery and "the mic hands us silence". Numbers and
 * short strings only, never speech; well under the core's 4 KB cap.
 *
 * The battery is ONE object, the core's (domovoi/satellite_health.py
 * `_battery`, the same one a laptop sends): `battery` = {percent 0-100,
 * charging, on_power}. `charging` while the level goes up; `on_power`
 * while the charger is plugged in (a battery held full counts, not
 * charging). Since 2026-10-25 also `plugged`, which charger, when on one.
 * Besides the minute's frame, a room sends one when the charger goes on or
 * off or the level moves 5 % (1 % when low; [BatteryReporter]), for the core's
 * low-battery warnings (domovoi/room_battery.py).
 */
object HealthSample {
    data class Facts(
        val seq: Int,
        val ts: Double,
        val rssKb: Long?,
        val cpuPct: Double?,
        /** The battery's temperature: the nearest a phone has to the Pi's SoC reading. */
        val tempC: Double?,
        val uptimeS: Long,
        val procUptimeS: Long,
        val coreOk: Boolean,
        val micExpected: Boolean,
        val micFps: Double,
        val idle: Boolean,
        val wsUp: Boolean,
        val wsDownForS: Long,
        val reconnects: Int,
        val wifiRxMbits: Double?,
        val wifiSsid: String?,
        val wifiSignalDbm: Int?,
        val batteryPercent: Int?,
        val batteryCharging: Boolean?,
        val onPower: Boolean?,
        val micBlocked: Boolean,
        val wakeLoopAgeS: Double?,
        val threads: Int?,
        /** Which charger (`battery.plugged`: "ac", "usb", "wireless", "dock"). */
        val plugged: String? = null,
    )

    fun build(f: Facts): JsonObject = buildJsonObject {
        put("type", "health")
        put("seq", f.seq)
        put("ts", f.ts)
        f.rssKb?.let { put("rss_kb", it) }
        f.threads?.let { put("threads", it) }
        f.cpuPct?.let { put("cpu_pct", round1(it)) }
        f.tempC?.let { put("soc_temp_c", round1(it)) }
        put("uptime_s", f.uptimeS)
        put("proc_uptime_s", f.procUptimeS)
        put("core_ok", f.coreOk)
        put("mic_expected", f.micExpected)
        put("mic_fps", round1(f.micFps))
        f.wakeLoopAgeS?.let { put("wake_loop_age_s", round1(it)) }
        put("idle", f.idle)
        put("ws", if (f.wsUp) "up" else "down")
        put("ws_down_for_s", f.wsDownForS)
        put("reconnects", f.reconnects)
        if (f.wifiRxMbits != null || f.wifiSsid != null || f.wifiSignalDbm != null) {
            put("wifi", buildJsonObject {
                f.wifiRxMbits?.let { put("rx_mbits", round1(it)) }
                f.wifiSsid?.let { put("ssid", it.take(64)) }
                f.wifiSignalDbm?.let { put("signal_dbm", it.coerceIn(-120, 0)) }
            })
        }
        val pct = f.batteryPercent?.coerceIn(0, 100)
        if (pct != null || f.batteryCharging != null || f.onPower != null) {
            put("battery", buildJsonObject {
                pct?.let { put("percent", it) }
                f.batteryCharging?.let { put("charging", it) }
                f.onPower?.let { put("on_power", it) }
                f.plugged?.let { put("plugged", it) }
            })
        }
        put("mic_blocked", f.micBlocked)
    }

    private fun round1(v: Double): Double = Math.round(v * 10.0) / 10.0
}
