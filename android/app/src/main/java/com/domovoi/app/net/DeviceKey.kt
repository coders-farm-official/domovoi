package com.domovoi.app.net

import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * The per-device key (1.5.0, PRIVATE-CHATS.md §2.1; docs/SECURITY_PRIVACY.md
 * "Private chats").
 *
 * The household token is the same on every phone and the device id is
 * self-asserted, so neither can say "this chat is THIS phone's". The server
 * mints a 256-bit device key the first time this phone's row registers
 * asking for one (`want_device_key`), answers it ONCE (`device_key`), and
 * keeps only its SHA-256. The phone keeps it, per server, sealed under an
 * Android Keystore key of its own (data/Prefs.kt), and sends it as
 * [DEVICE_KEY_HEADER] on the routes that decide whose something is
 * ([DeviceKeyRoutes]).
 *
 * Where it goes is the interceptor's decision alone (DeviceAuthInterceptor),
 * by the household token's rules made stricter: only to the ACTIVE server,
 * the very scheme, host and port it was issued by (never the drop-in
 * socket's other port), only after that server has proved its identity
 * (the claim key's L56 rule), never in room mode, never on a redirect to
 * anywhere else, and a header a caller set itself is always stripped. It is
 * never logged and never shown, and it never sits in a data class (whose
 * toString would print it): it is read straight out of the JSON answer.
 */
const val DEVICE_KEY_HEADER = "X-Domovoi-Device-Key"

/** The registration body's ask (core-0063: on EVERY register, at pairing
 *  and on every app start). Without it a 1.5.0 server mints no key. */
const val WANT_DEVICE_KEY = "want_device_key"

/**
 * The routes that carry the device key (core-0056, and the server routes
 * that read it through `devices.caller_device_row`): chat and its files,
 * the deliveries' catch-up read, the state socket, the phone admin grant,
 * a person's music mix, listening history, up next and the room plays that
 * say whose music a room follows, faces, the shared-screen checks of photo
 * search, and the device's own row ("this is me", the registration itself:
 * `device_key_ok` is only true when the registration presents it). Nothing
 * else: the key proves nothing anywhere else, so it goes nowhere else.
 */
object DeviceKeyRoutes {
    /** Whole-segment prefixes: `/api/chat` covers `/api/chat/threads/7`,
     *  never `/api/chatter`. */
    private val PREFIXES = listOf(
        "/api/chat",
        "/api/help/deliveries",
        "/ws/state",
        "/api/auth/phone-grant",
        "/api/music/listens",
        "/api/music/up-next",
        "/api/faces",
        "/api/media/search",
        "/api/devices",
    )

    /** The room plays that replace a room's queue (music.py `_note_room_start`). */
    private val EXACT = setOf(
        "/api/music/play",
        "/api/music/play-playlist",
        "/api/music/play-track",
        "/api/music/play-tracks",
    )

    /** `/api/people/{id}/music-mix`. */
    private val MUSIC_MIX = Regex("^/api/people/[^/]+/music-mix/?$")

    fun carries(path: String): Boolean {
        val p = path.substringBefore('?')
        if (p in EXACT) return true
        if (MUSIC_MIX.matches(p)) return true
        return PREFIXES.any { p == it || p.startsWith("$it/") }
    }
}

/** A device key as the server mints it (url-safe base64, 43 characters):
 *  printable ASCII only, or nothing. OkHttp throws on anything else, with
 *  the value in its message. */
internal fun headerSafeDeviceKey(key: String?): String? =
    key?.trim()?.takeIf { it.isNotEmpty() && it.length <= 200 && it.all { c -> c.code in 0x21..0x7E } }

/**
 * Where the device keys are kept, and which device id this phone uses, per
 * server (normalised address). [com.domovoi.app.data.Prefs] in the app; a
 * map in the tests.
 */
interface DeviceKeyBook {
    /** The key this phone holds for [server], or null. Never logged. */
    fun deviceKeyFor(server: String): String?

    /** Keep [key] for [server] (null: forget it). True once it is ON DISK:
     *  the key is answered once, so a write that didn't land is a key lost. */
    fun rememberDeviceKey(server: String, key: String?): Boolean

    /** The device id this phone registers under at [server]. */
    fun deviceIdFor(server: String): String

    /** The key for [server] is lost (`device_key_ok: false`): forget it and
     *  start over there under a NEW device id, which is returned. The old
     *  id's private chats stay on the server, for its admins. Other servers
     *  keep their ids and keys. */
    fun startOverAt(server: String): String
}

/** What a registration answer says about the device key. */
sealed interface DeviceKeyAnswer {
    /** Just minted: keep it. Comes once. */
    data class Minted(private val key: String) : DeviceKeyAnswer {
        /** The key itself, kept out of [toString]. */
        fun reveal(): String = key
        override fun toString(): String = "Minted(key=…)"
    }

    /** The key this phone presented is this row's: nothing to do. */
    data object Ok : DeviceKeyAnswer

    /** The row has a key and this phone didn't present it: lost. */
    data object Lost : DeviceKeyAnswer

    /** Nothing said: a server from before 1.5.0 (no field at all), or one
     *  that keeps no keys yet. The phone works keyless. */
    data object None : DeviceKeyAnswer
}

/** Read [answer]'s `device_key` / `device_key_ok` (devices.py register). */
fun deviceKeyAnswer(answer: JsonObject): DeviceKeyAnswer {
    val minted = headerSafeDeviceKey((answer["device_key"] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.contentOrNull)
    if (minted != null) return DeviceKeyAnswer.Minted(minted)
    return when ((answer["device_key_ok"] as? JsonPrimitive)?.takeIf { it !is JsonNull }?.booleanOrNull) {
        true -> DeviceKeyAnswer.Ok
        false -> DeviceKeyAnswer.Lost
        null -> DeviceKeyAnswer.None
    }
}

/** A fresh device id for a phone starting over: `android-` and 12 hex
 *  digits (the server takes 3–64 of `[A-Za-z0-9._:-]`). Longer than the
 *  first id's 4 so it can't land on another phone's row. */
fun newDeviceId(random: java.util.Random = java.security.SecureRandom()): String {
    val bytes = ByteArray(6).also { random.nextBytes(it) }
    return "android-" + bytes.joinToString("") { "%02x".format(it) }
}
