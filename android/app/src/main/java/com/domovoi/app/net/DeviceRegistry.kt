package com.domovoi.app.net

import android.os.Build
import com.domovoi.app.AppContainer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
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
 *
 * **Whose phone this is** ("this is me", 2026-11-12; web/backend/api/
 * devices.py `claim_decision`). The server decides, and every device answer
 * (the registration's and the claim's) says who the phone belongs to:
 * `owner_person_id` / `owner_person_name` / `owner_claimed_at`. That answer
 * is what the app stores and shows, per server ([rememberDeviceAnswer]),
 * never what it asked for, so a refused claim is never shown as the owner.
 * The registration no longer carries an owner at all: an app start must not
 * undo a claim made on the People page, in the dashboard, or by an admin.
 * Only [claimThisPhone] ("this is me" / "not me", and the Connection
 * panel's picker) changes it.
 */

@Serializable
data class DeviceRow(
    @SerialName("device_id") val deviceId: String = "",
    val name: String = "",
    val platform: String? = null,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    /** Whether an admin marked this device a shared screen (V014). Absent
     *  from a server older than shared screens — see [sharedScreenAnswer]. */
    @SerialName("shared_screen") val sharedScreen: Boolean? = null,
    @SerialName("owner_person_id") val ownerPersonId: Long? = null,
    @SerialName("owner_person_name") val ownerPersonName: String? = null,
    @SerialName("owner_claimed_at") val ownerClaimedAt: String? = null,
)

/** The person a server says this phone is: [id] on THAT server, the name
 *  it had in the answer, and when this phone itself last said "this is
 *  me" (null after an admin set it, or a claim from before keys). */
@Serializable
data class DeviceOwner(
    val id: Long,
    val name: String = "",
    val claimedAt: String? = null,
)

/** Where whose-phone-this-is and the claim key are kept, per server
 *  (normalised address). [com.domovoi.app.data.Prefs] in the app; a map in
 *  the tests. */
interface ClaimBook {
    fun deviceOwnerFor(server: String): DeviceOwner?
    fun claimKeyFor(server: String): String?

    /** Store [owner] (null: nobody) as the phone's owner at [server], and
     *  [claimKey] as the key it holds there (null: none). */
    fun rememberDeviceOwner(server: String, owner: DeviceOwner?, claimKey: String?)
    fun setSharedScreen(url: String, shared: Boolean)
}

/** The header the claim key goes in (devices.py CLAIM_KEY_HEADER). */
const val CLAIM_KEY_HEADER = "X-Domovoi-Claim-Key"

/** The header this phone names itself by on the calls that act as its
 *  person (the server's files.caller_device_id). */
const val DEVICE_ID_HEADER = "X-Device-Id"

/** The headers a call acting as this phone's person carries: its id, and
 *  the claim key when it holds one for that server. */
fun claimHeaders(deviceId: String, claimKey: String?): Map<String, String> = buildMap {
    put(DEVICE_ID_HEADER, deviceId)
    headerSafeClaimKey(claimKey)?.let { put(CLAIM_KEY_HEADER, it) }
}

/** A claim key as the server mints it (url-safe base64, 43 characters):
 *  printable ASCII only, or nothing — OkHttp throws on anything else, with
 *  the value in its message. */
internal fun headerSafeClaimKey(key: String?): String? =
    key?.trim()?.takeIf { it.isNotEmpty() && it.length <= 200 && it.all { c -> c.code in 0x21..0x7E } }

/**
 * What a device answer means for the phone at its server, or null when the
 * answer says nothing about whose it is (a server from before V035). The
 * web's DeviceIdentity.rememberOwner rule: the key comes once, in the
 * answer to the claim that minted it; a phone that is nobody's now, or
 * whose person changed without a new key (an admin set it), holds none.
 */
data class OwnerAnswer(val owner: DeviceOwner?, val claimKey: String?)

fun ownerAnswer(answer: JsonObject, before: DeviceOwner?, heldKey: String?): OwnerAnswer? {
    if ("owner_person_id" !in answer) return null
    val id = (answer["owner_person_id"] as? JsonPrimitive)?.longOrNull
    val next = id?.let {
        DeviceOwner(
            id = it,
            name = (answer["owner_person_name"] as? JsonPrimitive)?.contentOrNull.orEmpty(),
            claimedAt = (answer["owner_claimed_at"] as? JsonPrimitive)?.takeIf { p -> p !is JsonNull }?.contentOrNull,
        )
    }
    val minted = headerSafeClaimKey((answer["claim_key"] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull)
    val key = when {
        minted != null -> minted
        next == null -> null
        before != null && before.id != next.id -> null
        else -> heldKey
    }
    return OwnerAnswer(next, key)
}

/** Store what [answer] says about the phone at [server] (the server the
 *  call was made to, read before it: never the active one afterwards). */
fun rememberDeviceAnswer(book: ClaimBook, server: String, answer: JsonObject) {
    val row = runCatching { answer.decode<DeviceRow>() }.getOrNull() ?: return
    book.setSharedScreen(server, sharedScreenAnswer(row))
    ownerAnswer(answer, book.deviceOwnerFor(server), book.claimKeyFor(server))?.let {
        book.rememberDeviceOwner(server, it.owner, it.claimKey)
    }
}

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
 *  failure — the app works unnamed, queue entries just carry no tag. The
 *  answer also says whether this install is a shared screen and whose it
 *  is, which is remembered for the server that gave it (net/SharedScreen.kt,
 *  [rememberDeviceAnswer]). */
suspend fun registerDevice(app: AppContainer): DeviceRow? = runCatching {
    val row = registerWith(
        app.api, app.prefs, app.prefs.serverUrl.value, app.prefs.deviceId,
        suggestedDeviceName(), "Android ${Build.VERSION.RELEASE}; ${Build.MODEL}",
    )
    // The server answered: a good moment to send the songs waiting in the
    // listening-history outbox (listens/ListenLog.kt).
    app.listens.flushSoon()
    row
}.getOrNull()

/** The registration itself, for [server]: the body, the call (sent only
 *  while [server] is still the active one), and the answer stored for
 *  [server]. Throws on a failure. */
suspend fun registerWith(
    api: ApiClient,
    book: ClaimBook,
    server: String,
    deviceId: String,
    name: String,
    userAgent: String,
): DeviceRow {
    val answer = api.post("/api/devices/register", registerPayload(deviceId, name, userAgent), onlyTo = server)
    val obj = answer as? JsonObject ?: throw java.io.IOException("unexpected registration answer")
    rememberDeviceAnswer(book, server, obj)
    return obj.decode()
}

/**
 * The register body. No `owner_person_id`: left out, the server keeps the
 * person the phone already has. Sending it (as this app did until
 * 2026-11-12, `null` when the local "This phone belongs to" was unset)
 * would undo, on every app start, a claim made on the People page or by an
 * admin. Whose the phone is comes back in the answer instead.
 */
fun registerPayload(deviceId: String, name: String, userAgent: String): JsonObject =
    buildJsonObject {
        put("device_id", deviceId)
        put("name", name)
        put("platform", "android")
        put("user_agent", userAgent)
    }

/** Rename this device. Throws so the Settings panel can report the failure. */
suspend fun renameDevice(app: AppContainer, name: String): DeviceRow {
    val server = app.prefs.serverUrl.value
    val answer = app.api.patch(
        "/api/devices/${android.net.Uri.encode(app.prefs.deviceId)}",
        buildJsonObject { put("name", name) },
    )
    (answer as? JsonObject)?.let { rememberDeviceAnswer(app.prefs, server, it) }
    return answer.decode()
}

// ─── "This is me" ─────────────────────────────────────────────────────────

/**
 * "This is me" ([personId]) or "not me" (null) for THIS phone at [server]:
 * `PUT /api/devices/{id}/person` with `X-Device-Id` and, when one is held,
 * the claim key. The answer is stored for [server] ([rememberDeviceAnswer])
 * and returned: whose the phone is NOW, which is what the caller shows.
 *
 * Throws [ApiException] on a refusal (the caller shows the server's
 * sentence, [claimRefusal]). A refusal that says the phone can't prove
 * whose it is drops the stored key: it no longer works.
 */
suspend fun claimThisPhone(
    api: ApiClient,
    book: ClaimBook,
    server: String,
    deviceId: String,
    personId: Long?,
): DeviceOwner? {
    val path = "/api/devices/${encodePathSegment(deviceId)}/person"
    val body = buildJsonObject { if (personId == null) put("person_id", JsonNull) else put("person_id", personId) }
    val answer = try {
        api.put(path, body, headers = claimHeaders(deviceId, book.claimKeyFor(server)), onlyTo = server)
    } catch (e: ApiException) {
        if (claimRefusal(e.status, apiDetail(e)) == ClaimRefusal.CantProve) {
            book.rememberDeviceOwner(server, book.deviceOwnerFor(server), null)
        }
        throw e
    }
    val obj = answer as? JsonObject ?: throw java.io.IOException("unexpected claim answer")
    rememberDeviceAnswer(book, server, obj)
    return book.deviceOwnerFor(server)
}

/** The server's refusals of a claim (the hand-off's table), by what the app
 *  does about each. */
enum class ClaimRefusal {
    /** Another phone or browser already has the person: an admin's yes. */
    TakenByAnother,
    /** The id in the path isn't this phone: a bug. */
    NotThisDevice,
    /** No claim key, or the wrong one: drop it, offer "this is me". */
    CantProve,
    /** An admin marked this phone a shared screen. */
    SharedScreen,
    /** The person (or the device row) is gone: refresh People. */
    Gone,
    Other,
}

fun claimRefusal(status: Int, detail: String?): ClaimRefusal {
    val d = detail.orEmpty()
    return when {
        status == 409 -> ClaimRefusal.SharedScreen
        status == 404 -> ClaimRefusal.Gone
        status != 403 -> ClaimRefusal.Other
        d.contains("already claimed by another phone or browser") -> ClaimRefusal.TakenByAnother
        d.contains("only that phone or browser itself") -> ClaimRefusal.NotThisDevice
        d.contains("can't prove it is") || d.contains("say \"this is me\" again") -> ClaimRefusal.CantProve
        else -> ClaimRefusal.Other
    }
}

/** The `detail` sentence of a refusal, or null. */
fun apiDetail(e: ApiException): String? = runCatching {
    ((DomovoiJson.parseToJsonElement(e.body) as? JsonObject)?.get("detail"))?.jsonPrimitive?.contentOrNull
}.getOrNull()?.takeIf { it.isNotBlank() }

/** A device id as one URL path segment (they are `[A-Za-z0-9._:-]`, but
 *  never trust that on the way out). Pure, unlike android.net.Uri. */
internal fun encodePathSegment(s: String): String =
    java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")
