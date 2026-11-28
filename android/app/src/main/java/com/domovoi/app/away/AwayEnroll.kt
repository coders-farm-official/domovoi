package com.domovoi.app.away

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put
import java.util.Base64

/*
 * Setting away chat up, over the home network only (AWAY-CHAT-WIRE §1):
 * POST / DELETE /api/away/enroll and GET /api/away/status on the
 * dashboard's API, with the phone's identity as at home (the household
 * token and the device key; net/DeviceKey.kt carries the key on /api/away).
 */

/** The dashboard's API as the enrolment needs it: the app's ApiClient, or
 *  OkHttp in the loopback test. A refusal throws [AwayHttpError]. */
interface AwayHttp {
    suspend fun post(path: String, body: JsonObject): JsonElement
    suspend fun get(path: String): JsonElement
    suspend fun delete(path: String): JsonElement
}

class AwayHttpError(val status: Int, val body: String) : java.io.IOException("HTTP $status")

/** Where the enrolment is kept for one server (data/Prefs.kt; a map in tests). */
interface AwayKeyBook {
    fun awayEnrolmentFor(server: String): AwayEnrolment?

    /** Keep [e] for [server] (null: wipe it). True once it is on disk. */
    fun rememberAwayEnrolment(server: String, e: AwayEnrolment?): Boolean
}

/** `GET /api/away/status`. */
data class AwayHouseStatus(
    /** `on`, `away_off` or `internet_off`. */
    val house: String,
    val enrolled: Boolean,
    /** `ready`, `starting`, or null. */
    val state: String?,
) {
    /** What the app says about it, or null when all is well. */
    val line: String?
        get() = when {
            house == "away_off" -> AwayText.AWAY_OFF
            house == "internet_off" -> AwayText.INTERNET_OFF
            !enrolled -> AwayText.SET_UP_AGAIN
            else -> null
        }
}

/** How setting away chat up went. */
sealed interface EnrollResult {
    data class Ok(val enrolment: AwayEnrolment) : EnrollResult

    /** The house said no: its [code] (`away_off`, `internet_off`, `guest`,
     *  `lan_only`, `bad_key`, `device_key_required`, …) and the plain [line]. */
    data class Refused(val status: Int, val code: String?, val line: String) : EnrollResult
}

object AwayEnroll {
    const val ENROLL = "/api/away/enroll"
    const val STATUS = "/api/away/status"

    /** The line for an enrolment refusal (§1's table and §8 item 6). */
    fun refusalLine(status: Int, code: String?): String = when {
        code == "away_off" -> AwayText.AWAY_OFF
        code == "internet_off" -> AwayText.INTERNET_OFF
        code == "guest" -> "Chat from away is for household members' phones, not shared screens or guests."
        code == "lan_only" -> "Set chat from away up on your home network."
        code == "device_key_required" -> "This phone needs to register with your house again first. Open the app at home and try again."
        status == 501 -> "Your house isn't set up yet. An admin sets it up on the dashboard first."
        status == 404 || status == 405 -> "Your house doesn't have chat from away yet: it comes with Domovoi 1.5.0."
        status == 401 || status == 403 -> "Your house didn't accept this phone. Pair it again in Settings."
        else -> AwayText.WRONG
    }

    private fun codeOf(body: String): String? = runCatching {
        (AwayWire.json.parseToJsonElement(body) as? JsonObject)?.get("code")?.let { it as? JsonPrimitive }?.contentOrNull
    }.getOrNull()

    /** The house's answer as what the phone keeps, or null when it doesn't
     *  fit (then nothing is kept and the person is told something went wrong). */
    fun enrolmentOf(answer: JsonObject, keys: AwayKeyPairs): AwayEnrolment? {
        fun s(k: String) = (answer[k] as? JsonPrimitive)?.contentOrNull
        val onion = s("onion")?.lowercase() ?: return null
        val port = (answer["port"] as? JsonPrimitive)?.intOrNull ?: return null
        val house = s("house_noise_pub") ?: return null
        val proto = (answer["proto"] as? JsonPrimitive)?.intOrNull ?: return null
        if (!AwayKeys.isOnion(onion) || port !in 1..65535 || AwayKeys.unb64url(house, 32) == null) return null
        val enc = Base64.getEncoder()
        return AwayEnrolment(
            torPrivate = enc.encodeToString(keys.torPrivate),
            noisePrivate = enc.encodeToString(keys.noisePrivate),
            onion = onion, port = port, houseNoisePub = house, proto = proto, state = s("state"),
        )
    }

    /**
     * Make two fresh key pairs, send their public halves, and keep the
     * private halves with the house's answer for [server]. Enrolling again
     * replaces both keys (§1). Nothing is kept unless the house said yes.
     */
    suspend fun enroll(http: AwayHttp, book: AwayKeyBook, server: String, keys: AwayKeyPairs = AwayKeyPairs.generate()): EnrollResult {
        val body = buildJsonObject {
            put("tor_auth_pub", keys.torAuthPub)
            put("noise_pub", keys.noisePub)
        }
        val answer = try {
            http.post(ENROLL, body) as? JsonObject
        } catch (e: AwayHttpError) {
            val code = codeOf(e.body)
            return EnrollResult.Refused(e.status, code, refusalLine(e.status, code))
        } ?: return EnrollResult.Refused(200, null, AwayText.WRONG)
        val e = enrolmentOf(answer, keys) ?: return EnrollResult.Refused(200, null, AwayText.WRONG)
        if (e.proto != AwayWire.PROTO) return EnrollResult.Refused(200, "proto", AwayText.WRONG)
        if (!book.rememberAwayEnrolment(server, e)) return EnrollResult.Refused(200, "storage", AwayText.WRONG)
        return EnrollResult.Ok(e)
    }

    /** Turn away chat off for this phone: the house revokes its keys
     *  (always `{"ok": true}`), and the phone wipes them whatever the house
     *  answered. True when the house heard it. */
    suspend fun unenroll(http: AwayHttp, book: AwayKeyBook, server: String): Boolean {
        val told = runCatching { http.delete(ENROLL) }.isSuccess
        book.rememberAwayEnrolment(server, null)
        return told
    }

    /** `GET /api/away/status`, or null when it couldn't be read. */
    suspend fun status(http: AwayHttp): AwayHouseStatus? = runCatching {
        val o = http.get(STATUS) as JsonObject
        AwayHouseStatus(
            house = (o["house"] as? JsonPrimitive)?.contentOrNull ?: "on",
            enrolled = (o["enrolled"] as? JsonPrimitive)?.booleanOrNull == true,
            state = (o["state"] as? JsonPrimitive)?.contentOrNull,
        )
    }.getOrNull()
}
