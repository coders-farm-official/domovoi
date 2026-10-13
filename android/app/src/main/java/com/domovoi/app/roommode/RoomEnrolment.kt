package com.domovoi.app.roommode

import com.domovoi.app.net.CleartextPolicy
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.text.Normalizer
import java.util.Base64

/**
 * What the dashboard's "Add a spare device" hands a phone that is to become
 * a room: the core to dial, the fingerprint of the server identity to hold
 * it to, and the room it is for. The shared payload (the Linux install
 * command carries the same three values):
 *
 *     domovoi-room:1?core=<core base URL>&fp=<SHA256:… fingerprint>&room=<room name>
 *
 * percent-encoded UTF-8, with no secret in it: the room's pairing key is
 * minted on the phone and only its hash ever reaches the server, after a
 * person approves the device on the dashboard. The fingerprint is what
 * makes the first connect safe — the phone refuses any server that cannot
 * prove that key (net-free half: [RoomIdentity]).
 *
 * Pure (no Android): parsed the same from a scanned QR code, a pasted line
 * and a `domovoi-room:` link another app hands over.
 */
data class Enrolment(
    /** `http(s)://host:port` of the core (port 6370), no trailing slash. */
    val coreUrl: String,
    /** `SHA256:<base64>` of the core's Ed25519 key, as the dashboard shows it. */
    val fingerprint: String,
    /** The room id the phone connects as: `^[a-z][a-z0-9_]{0,31}$`. */
    val roomId: String,
    /** The room's name as given (shown on the room screen). */
    val roomLabel: String,
)

object RoomEnrolment {
    const val SCHEME = "domovoi-room"
    const val VERSION = "1"

    /** The core's room-id rule (domovoi/main.py `_ROOM_ID_RE`). */
    val ROOM_ID = Regex("^[a-z][a-z0-9_]{0,31}$")

    /** Longest payload accepted (a QR code holds far less). */
    const val MAX_PAYLOAD = 1024

    /** Longest room name kept (the dashboard's room_label limit). */
    const val MAX_ROOM_LABEL = 80

    sealed class Result {
        data class Ok(val enrolment: Enrolment) : Result()

        /** Why the text is not an enrolment, in words for the screen. */
        data class Invalid(val reason: String) : Result()
    }

    fun parse(text: String?): Result {
        val raw = text?.trim().orEmpty()
        if (raw.isEmpty()) return Result.Invalid("nothing to read")
        if (raw.length > MAX_PAYLOAD) return Result.Invalid("too long to be a room code")
        val colon = raw.indexOf(':')
        if (colon < 0 || !raw.substring(0, colon).equals(SCHEME, ignoreCase = true)) {
            return Result.Invalid("not a Domovoi room code (it should start with $SCHEME:)")
        }
        val rest = raw.substring(colon + 1)
        val q = rest.indexOf('?')
        val version = if (q < 0) rest else rest.substring(0, q)
        if (version != VERSION) {
            return if (version.toIntOrNull()?.let { it > 1 } == true) {
                Result.Invalid("this room code is from a newer Domovoi; update the app")
            } else {
                Result.Invalid("unknown room code version")
            }
        }
        if (q < 0) return Result.Invalid("the room code carries no server")
        val params = mutableMapOf<String, String>()
        for (pair in rest.substring(q + 1).split('&')) {
            if (pair.isEmpty()) continue
            val eq = pair.indexOf('=')
            val key = percentDecode(if (eq < 0) pair else pair.substring(0, eq))
                ?: return Result.Invalid("the room code is not readable (bad %-encoding)")
            val value = percentDecode(if (eq < 0) "" else pair.substring(eq + 1))
                ?: return Result.Invalid("the room code is not readable (bad %-encoding)")
            if (key in params) return Result.Invalid("the room code names $key twice")
            params[key] = value
        }
        val core = params["core"]?.trim()?.takeIf { it.isNotEmpty() }
            ?: return Result.Invalid("the room code carries no server")
        val coreUrl = normalizeCore(core) ?: return Result.Invalid("the server address in the room code is not usable")
        val host = coreUrl.toHttpUrlOrNull()?.host
        if (!coreUrl.startsWith("https://") && !CleartextPolicy.cleartextPermitted(host)) {
            return Result.Invalid(CleartextPolicy.refusalMessage(host ?: core))
        }
        val fp = params["fp"]?.trim()?.takeIf { it.isNotEmpty() }
            ?: return Result.Invalid("the room code carries no server fingerprint")
        val fingerprint = normalizeFingerprint(fp)
            ?: return Result.Invalid("the server fingerprint in the room code is malformed")
        val room = params["room"]?.trim()?.takeIf { it.isNotEmpty() }
            ?: return Result.Invalid("the room code names no room")
        if (room.length > MAX_ROOM_LABEL) return Result.Invalid("the room name is too long")
        val roomId = roomIdFor(room) ?: return Result.Invalid("the room name has no letters to make a room from")
        return Result.Ok(Enrolment(coreUrl, fingerprint, roomId, room))
    }

    /**
     * The room id for a name: the name itself when it already is one
     * (`kitchen`, `living_room`), else a slug of it — lower case, accents
     * dropped, every run of other characters one `_`, a leading digit
     * prefixed with `room_`, at most 32 characters ("Living Room" →
     * `living_room`, "Kid's room 2" → `kid_s_room_2`). Null when nothing is
     * left.
     */
    fun roomIdFor(name: String): String? {
        val trimmed = name.trim()
        if (ROOM_ID.matches(trimmed)) return trimmed
        val ascii = Normalizer.normalize(trimmed, Normalizer.Form.NFKD)
            .replace(Regex("\\p{M}+"), "")
            .lowercase(java.util.Locale.ROOT)
        var slug = ascii.replace(Regex("[^a-z0-9]+"), "_").trim('_')
        if (slug.isEmpty()) return null
        if (slug[0].isDigit()) slug = "room_$slug"
        slug = slug.take(32).trimEnd('_')
        return slug.takeIf { ROOM_ID.matches(it) }
    }

    /** `SHA256:` + the base64 of 32 bytes (padding optional), or null. */
    fun normalizeFingerprint(value: String): String? {
        val text = value.trim()
        if (!text.startsWith(RoomIdentity.FINGERPRINT_PREFIX) || text.length > 128) return null
        val b64 = text.removePrefix(RoomIdentity.FINGERPRINT_PREFIX).trimEnd('=')
        if (b64.length != 43 || !b64.all { it.isLetterOrDigit() && it.code < 128 || it == '+' || it == '/' }) return null
        val bytes = runCatching { Base64.getDecoder().decode("$b64=") }.getOrNull() ?: return null
        if (bytes.size != 32) return null
        return RoomIdentity.FINGERPRINT_PREFIX + b64
    }

    /** `http(s)://host:port` (the port always spelled out) with nothing
     *  after it, or null. */
    fun normalizeCore(value: String): String? {
        val url = value.trim().toHttpUrlOrNull() ?: return null
        if (url.username.isNotEmpty() || url.password.isNotEmpty()) return null
        if (url.query != null || url.fragment != null) return null
        if (url.encodedPath != "/") return null
        val host = if (url.host.contains(':')) "[${url.host}]" else url.host
        return "${url.scheme}://$host:${url.port}"
    }

    /**
     * Percent-decoding of one query component as UTF-8, `+` read as a space
     * (a real plus is always `%2B`: the fingerprint's base64 has them).
     * Null for a broken escape or bytes that are not UTF-8.
     */
    internal fun percentDecode(s: String): String? {
        val out = ByteArrayOutputStream(s.length)
        var i = 0
        while (i < s.length) {
            val c = s[i]
            when {
                c == '%' -> {
                    if (i + 2 >= s.length) return null
                    val hi = Character.digit(s[i + 1], 16)
                    val lo = Character.digit(s[i + 2], 16)
                    if (hi < 0 || lo < 0) return null
                    out.write(hi * 16 + lo)
                    i += 3
                }
                c == '+' -> { out.write(' '.code); i++ }
                else -> {
                    out.write(c.toString().toByteArray(Charsets.UTF_8))
                    i++
                }
            }
        }
        return try {
            Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(out.toByteArray()))
                .toString()
        } catch (_: CharacterCodingException) {
            null
        }
    }
}
