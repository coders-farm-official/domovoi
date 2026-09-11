package com.domovoi.app.net

import android.os.Build
import com.domovoi.app.AppContainer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * This install's identity on the server — the Android half of the web's
 * DeviceIdentity (web/static/data.js).
 *
 * The id is the one [com.domovoi.app.data.Prefs.deviceId] already mints for
 * resume positions, so a phone is ONE device everywhere: renaming it relabels
 * its room-queue entries, and an admin block on it covers both features.
 *
 * [registerDevice] is idempotent and safe on every launch — the server seeds
 * `name` only when the row is new, so sending the hardware default can't stomp
 * a name someone chose.
 */

@Serializable
data class DeviceRow(
    @SerialName("device_id") val deviceId: String = "",
    val name: String = "",
    val platform: String? = null,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
)

/** "Pixel 8" rather than "google/shiba" — what a person would recognise in a
 *  queue. Only a SEED; the real answer is whatever they type in Settings. */
fun suggestedDeviceName(): String {
    val model = (Build.MODEL ?: "").trim()
    val brand = (Build.MANUFACTURER ?: "").trim()
    return when {
        model.isEmpty() && brand.isEmpty() -> "Android phone"
        model.isEmpty() -> brand.replaceFirstChar { it.uppercase() }
        // Most vendors already prefix the model with the brand ("Pixel 8" on a
        // google device, "SM-S911B" on samsung) — don't say it twice.
        brand.isEmpty() || model.lowercase().startsWith(brand.lowercase()) -> model
        else -> "${brand.replaceFirstChar { it.uppercase() }} $model"
    }
}

/** Upsert this device's row and refresh last_seen_at. Returns null on any
 *  failure — the app works unnamed, queue entries just carry no tag. */
suspend fun registerDevice(app: AppContainer): DeviceRow? = runCatching {
    app.api.post(
        "/api/devices/register",
        buildJsonObject {
            put("device_id", app.prefs.deviceId)
            put("name", suggestedDeviceName())
            put("platform", "android")
            put("user_agent", "Android ${Build.VERSION.RELEASE}; ${Build.MODEL}")
        },
    ).decode<DeviceRow>()
}.getOrNull()

/** Rename this device. Throws so the Settings panel can report the failure. */
suspend fun renameDevice(app: AppContainer, name: String): DeviceRow =
    app.api.patch(
        "/api/devices/${android.net.Uri.encode(app.prefs.deviceId)}",
        buildJsonObject { put("name", name) },
    ).decode()
