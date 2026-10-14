package com.domovoi.app.roommode

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.text.Normalizer

/**
 * What the dashboard's Settings → Satellites → "Add a spare device" hands a
 * phone that is to become a room: the core to dial, the fingerprint of the
 * server identity to hold it to, and the room it is for. The shared
 * payload (the Linux install command carries the same string):
 *
 *     domovoi-room:1?core=<core base URL>&fp=<SHA256:… fingerprint>&room=<room name>
 *
 * percent-encoded UTF-8, with no secret in it: the room's pairing key is
 * minted on the phone and only its hash ever reaches the server, after a
 * person approves the device on the dashboard. The fingerprint is what
 * makes the first connect safe — the phone refuses any server that cannot
 * prove that key ([RoomIdentity]).
 *
 * A strict, line-for-line port of the reference parser,
 * `satellite/enrolment.py` (wf/sat-pc), and held to the same contract,
 * `docs/uml/spare-device-enrol-fixture.json`: the same normalised values,
 * the same room ids, the same reason codes for the same inputs
 * (RoomEnrolmentTest). Pure (no Android).
 */
data class Enrolment(
    /** `http(s)://host[:port]`, scheme and host lower-case, no trailing slash. */
    val coreUrl: String,
    /** `SHA256:<43 base64 characters>` of the core's Ed25519 key. */
    val fingerprint: String,
    /** The room id the phone connects as: `^[a-z][a-z0-9_]{0,31}$`. */
    val roomId: String,
    /** The room's name exactly as the payload carried it. */
    val roomLabel: String,
) {
    /** The satellite socket's base: the core URL with ws/wss. */
    val wsUrl: String get() = if (coreUrl.startsWith("https://")) "wss://" + coreUrl.removePrefix("https://") else "ws://" + coreUrl.removePrefix("http://")
}

object RoomEnrolment {
    const val SCHEME = "domovoi-room"
    const val VERSION = "1"
    const val MAX_PAYLOAD_LEN = 1024
    const val MAX_CORE_LEN = 256

    /** In characters (code points), after decoding. */
    const val MAX_ROOM_NAME_LEN = 64
    const val MAX_ROOM_ID_LEN = 32

    /** The core's room-id rule (domovoi/main.py `_ROOM_ID_RE`). */
    val ROOM_ID = Regex("^[a-z][a-z0-9_]{0,31}$")
    private val FP = Regex("^SHA256:[A-Za-z0-9+/]{43}$")
    private val KEY = Regex("^[a-z][a-z0-9_]{0,31}$")
    private val HOST_NAME = Regex("[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
    private const val ROOM_ID_DIGIT_PREFIX = "room_"
    private const val HEX = "0123456789abcdefABCDEF"

    /** RFC 3986 query characters a value may carry raw. `+` is a plus sign. */
    private val RAW_OK: Set<Char> =
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" + "-._~" + "!$'()*+,;" + ":@/?").toSet()

    /** Dropped from a room name before its id is made ("Kid's" → kids). */
    private val APOSTROPHES = setOf('\'', '’')

    /** The core's LAN names (domovoi/transport_guard.py, less .localhost). */
    val HOME_SUFFIXES = listOf(".local", ".home.arpa", ".lan", ".internal")

    /** Why a payload was refused: the contract's stable reason codes. */
    enum class Reason(val code: String) {
        NOT_ENROLMENT("not_enrolment"),
        UNSUPPORTED_VERSION("unsupported_version"),
        MALFORMED("malformed"),
        MISSING_CORE("missing_core"),
        MISSING_FP("missing_fp"),
        MISSING_ROOM("missing_room"),
        BAD_CORE("bad_core"),
        NOT_HOME_NETWORK("not_home_network"),
        BAD_FP("bad_fp"),
        BAD_ROOM("bad_room"),
    }

    sealed class Result {
        data class Ok(val enrolment: Enrolment) : Result()

        /** Why the text is not an enrolment: the code, and words for the screen. */
        data class Invalid(val reasonCode: Reason, val reason: String) : Result()
    }

    private class Refused(val why: Reason, message: String) : Exception(message)

    fun parse(text: String?): Result = try {
        Result.Ok(parseOrThrow(text ?: ""))
    } catch (r: Refused) {
        Result.Invalid(r.why, r.message ?: r.why.code)
    }

    private fun parseOrThrow(payload: String): Enrolment {
        // A paste brings a newline or a space along; only those are trimmed.
        val text = payload.trim(' ', '\t', '\r', '\n')
        val colon = text.indexOf(':')
        if (colon < 0 || !asciiLower(text.substring(0, colon)).equals(SCHEME)) {
            throw Refused(Reason.NOT_ENROLMENT, "this is not a Domovoi room code (it starts with $SCHEME:)")
        }
        if (text.codePointCount(0, text.length) > MAX_PAYLOAD_LEN) {
            throw Refused(Reason.MALFORMED, "the room code is longer than $MAX_PAYLOAD_LEN characters")
        }
        val rest = text.substring(colon + 1)
        val q = rest.indexOf('?')
        val version = if (q < 0) rest else rest.substring(0, q)
        if (version.isEmpty() || !version.all { it in '0'..'9' }) {
            throw Refused(Reason.MALFORMED, "the room code has no version (domovoi-room:1?…)")
        }
        if (version != VERSION) {
            throw Refused(Reason.UNSUPPORTED_VERSION, "this room code is version $version; this app understands $VERSION — update the app")
        }
        if (q < 0) throw Refused(Reason.MALFORMED, "the room code has no fields")
        val query = rest.substring(q + 1)
        if ('#' in query) throw Refused(Reason.MALFORMED, "the room code must not carry a fragment (#)")
        val fields = LinkedHashMap<String, String>()
        for (pair in query.split('&')) {
            val eq = pair.indexOf('=')
            val key = if (eq < 0) pair else pair.substring(0, eq)
            if (eq < 0 || !KEY.matches(key)) throw Refused(Reason.MALFORMED, "'${pair.take(40)}' is not a key=value field")
            if (key in fields) throw Refused(Reason.MALFORMED, "the field $key is given twice")
            fields[key] = decode(pair.substring(eq + 1))
        }
        val missing = mapOf("core" to Reason.MISSING_CORE, "fp" to Reason.MISSING_FP, "room" to Reason.MISSING_ROOM)
        for ((key, why) in missing) {
            if (fields[key].isNullOrEmpty()) throw Refused(why, "the room code has no $key")
        }
        val core = normalizeCore(fields.getValue("core"))
        val fp = fields.getValue("fp")
        if (!FP.matches(fp)) throw Refused(Reason.BAD_FP, "the fingerprint must be SHA256: and 43 base64 characters")
        val room = checkRoomName(fields.getValue("room"))
        return Enrolment(core, fp, roomIdOrThrow(room), room)
    }

    // ── the room id ─────────────────────────────────────────────────────

    /**
     * The room id a room named [name] connects as (`room_id_for`): NFKD,
     * combining marks (Mn) and apostrophes (' and ’) dropped, A-Z lower-cased,
     * a-z and 0-9 kept, every other run of characters one `_` with none at
     * either end, `room_` before a leading digit; null when nothing is left
     * or it is over 32 characters. ASCII rules on purpose, so every runtime
     * reaches the same id.
     */
    fun roomIdFor(name: String): String? = try {
        roomIdOrThrow(name)
    } catch (_: Refused) {
        null
    }

    private fun roomIdOrThrow(name: String): String {
        val out = StringBuilder()
        var gap = false
        val nfkd = Normalizer.normalize(name, Normalizer.Form.NFKD)
        var i = 0
        while (i < nfkd.length) {
            val cp = nfkd.codePointAt(i)
            i += Character.charCount(cp)
            if (Character.getType(cp) == Character.NON_SPACING_MARK.toInt()) continue
            if (cp < 0x10000 && cp.toChar() in APOSTROPHES) continue
            var c = cp
            if (c in 'A'.code..'Z'.code) c += 32
            if (c in 'a'.code..'z'.code || c in '0'.code..'9'.code) {
                if (gap && out.isNotEmpty()) out.append('_')
                gap = false
                out.append(c.toChar())
            } else {
                gap = true
            }
        }
        var id = out.toString()
        if (id.isNotEmpty() && id[0].isDigit()) id = ROOM_ID_DIGIT_PREFIX + id
        if (id.isEmpty()) throw Refused(Reason.BAD_ROOM, "the room name has no letter or digit to make a room id from — add one (A-Z or 0-9)")
        if (id.length > MAX_ROOM_ID_LEN) throw Refused(Reason.BAD_ROOM, "the room id '$id' is longer than $MAX_ROOM_ID_LEN characters — pick a shorter name")
        check(ROOM_ID.matches(id)) { id }
        return id
    }

    /** [name] as a payload may carry it (`check_room_name`): not empty, at
     *  most 64 code points, no control or format characters (Cc, Cf), and a
     *  room id can be made from it. Not trimmed. */
    private fun checkRoomName(name: String): String {
        if (name.isEmpty()) throw Refused(Reason.BAD_ROOM, "the room name is empty")
        if (name.codePointCount(0, name.length) > MAX_ROOM_NAME_LEN) {
            throw Refused(Reason.BAD_ROOM, "the room name is longer than $MAX_ROOM_NAME_LEN characters")
        }
        var i = 0
        while (i < name.length) {
            val cp = name.codePointAt(i)
            i += Character.charCount(cp)
            val t = Character.getType(cp)
            if (t == Character.CONTROL.toInt() || t == Character.FORMAT.toInt()) {
                throw Refused(Reason.BAD_ROOM, "the room name has control characters")
            }
        }
        roomIdOrThrow(name)
        return name
    }

    /** A room name a person typed on this phone (trimmed, as the dashboard's
     *  builder trims), or null with the room-name rules' reason. */
    fun typedRoomName(typed: String): Result.Invalid? = try {
        checkRoomName(typed.trim())
        null
    } catch (r: Refused) {
        Result.Invalid(r.why, r.message ?: r.why.code)
    }

    // ── the core URL ────────────────────────────────────────────────────

    /** `normalize_core`: http(s); a host name, IPv4 or bracketed IPv6 (no
     *  zone id); an optional port 1-65535; nothing else. Scheme and host
     *  lower-case, no trailing slash. A well-formed address off the home
     *  network is `not_home_network`. */
    private fun normalizeCore(text: String): String {
        if (text.isEmpty() || text.length > MAX_CORE_LEN) throw Refused(Reason.BAD_CORE, "the server address is empty or too long")
        if (text.any { it.code < 0x21 || it.code > 0x7E }) throw Refused(Reason.BAD_CORE, "the server address has spaces or non-ASCII characters")
        val lower = asciiLower(text)
        val scheme = when {
            lower.startsWith("https://") -> "https"
            lower.startsWith("http://") -> "http"
            else -> throw Refused(Reason.BAD_CORE, "the server address must start with http:// or https://")
        }
        val after = text.substring(scheme.length + 3)
        val end = after.indexOfFirst { it == '/' || it == '?' || it == '#' }
        val netloc = if (end < 0) after else after.substring(0, end)
        val path = if (end < 0) "" else after.substring(end)
        if ('@' in netloc) throw Refused(Reason.BAD_CORE, "the server address must not carry a user name")
        if ((path != "" && path != "/") || '?' in text || '#' in text) {
            throw Refused(Reason.BAD_CORE, "the server address must be just scheme://host:port")
        }
        val host: String
        val shown: String
        val rest: String
        if (netloc.startsWith("[")) {
            val close = netloc.indexOf(']')
            if (close < 0) throw Refused(Reason.BAD_CORE, "the server address has an unclosed IPv6 bracket")
            host = asciiLower(netloc.substring(1, close))
            rest = netloc.substring(close + 1)
            if ('%' in host) throw Refused(Reason.BAD_CORE, "an IPv6 zone id (%…) is not supported")
            if (ipv6Bytes(host) == null) throw Refused(Reason.BAD_CORE, "'$host' is not an IPv6 address")
            shown = "[$host]"
        } else {
            val c = netloc.indexOf(':')
            host = asciiLower(if (c < 0) netloc else netloc.substring(0, c))
            rest = if (c < 0) "" else netloc.substring(c)
            if (host.isEmpty() || !HOST_NAME.matches(host)) throw Refused(Reason.BAD_CORE, "'$host' is not a host name or IPv4 address")
            shown = host
        }
        var port = ""
        if (rest.isNotEmpty()) {
            val digits = rest.substring(1)
            if (!rest.startsWith(":") || digits.isEmpty() || !digits.all { it in '0'..'9' }) {
                throw Refused(Reason.BAD_CORE, "the server address has a bad port")
            }
            val number = digits.trimStart('0').ifEmpty { "0" }.let { if (it.length > 5) 0 else it.toInt() }
            if (number !in 1..65535) throw Refused(Reason.BAD_CORE, "the server address's port is out of range")
            port = ":$number"
        }
        if (!isHomeNetworkHost(host)) {
            throw Refused(
                Reason.NOT_HOME_NETWORK,
                "$host is not on the home network (a private address, or a name ending in .local, .home.arpa, .lan " +
                    "or .internal): a room must not stream its microphone out of the house",
            )
        }
        return "$scheme://$shown$port"
    }

    /**
     * `is_home_network_host`: an RFC 1918 IPv4 address, an IPv6 unique-local
     * (fc00::/7) or link-local (fe80::/10) address, or a name ending in
     * .local, .home.arpa, .lan or .internal with something before it. Not
     * loopback, not 100.64/10, not 169.254/16, not a public address or name,
     * even over https: a room code decides where a room's microphone goes.
     */
    fun isHomeNetworkHost(host: String): Boolean {
        ipv4Bytes(host)?.let { b ->
            val a = b[0]
            val s = b[1]
            return a == 10 || (a == 172 && s in 16..31) || (a == 192 && s == 168)
        }
        if (':' in host) {
            val b = ipv6Bytes(host) ?: return false
            return (b[0] and 0xfe) == 0xfc || (b[0] == 0xfe && (b[1] and 0xc0) == 0x80)
        }
        return HOME_SUFFIXES.any { host.endsWith(it) && host.length > it.length }
    }

    /** Python's `ipaddress.IPv4Address`: four decimal octets, no leading zeros. */
    internal fun ipv4Bytes(text: String): IntArray? {
        val parts = text.split('.')
        if (parts.size != 4) return null
        val out = IntArray(4)
        for ((i, p) in parts.withIndex()) {
            if (p.isEmpty() || p.length > 3 || !p.all { it in '0'..'9' }) return null
            if (p.length > 1 && p[0] == '0') return null
            val v = p.toInt()
            if (v > 255) return null
            out[i] = v
        }
        return out
    }

    /** Python's `ipaddress.IPv6Address` (no zone id): 16 bytes, or null. */
    internal fun ipv6Bytes(text: String): IntArray? {
        if (text.isEmpty() || text.count { it == ':' } < 2) return null
        val halves = text.split("::")
        if (halves.size > 2) return null
        fun groups(part: String, allowV4Tail: Boolean): List<Int>? {
            if (part.isEmpty()) return emptyList()
            val pieces = part.split(':')
            val out = ArrayList<Int>()
            for ((i, p) in pieces.withIndex()) {
                if (i == pieces.lastIndex && allowV4Tail && '.' in p) {
                    val v4 = ipv4Bytes(p) ?: return null
                    out += (v4[0] shl 8) or v4[1]
                    out += (v4[2] shl 8) or v4[3]
                    continue
                }
                if (p.isEmpty() || p.length > 4 || !p.all { it in HEX }) return null
                out += p.toInt(16)
            }
            return out
        }
        val all: List<Int> = if (halves.size == 2) {
            val head = groups(halves[0], allowV4Tail = false) ?: return null
            val tail = groups(halves[1], allowV4Tail = true) ?: return null
            if (head.size + tail.size > 7) return null
            head + List(8 - head.size - tail.size) { 0 } + tail
        } else {
            val g = groups(text, allowV4Tail = true) ?: return null
            if (g.size != 8) return null
            g
        }
        return IntArray(16) { i -> if (i % 2 == 0) all[i / 2] shr 8 else all[i / 2] and 0xff }
    }

    // ── percent-encoding ────────────────────────────────────────────────

    /** One value, strictly: raw only what a URI query may carry, every `%`
     *  two hex digits, the bytes UTF-8. `+` is a plus sign, never a space. */
    internal fun decode(value: String): String {
        val raw = ByteArrayOutputStream(value.length)
        var i = 0
        while (i < value.length) {
            val ch = value[i]
            if (ch == '%') {
                if (i + 2 >= value.length) throw Refused(Reason.MALFORMED, "a %-escape is not two hex digits")
                val a = value[i + 1]
                val b = value[i + 2]
                if (a !in HEX || b !in HEX) throw Refused(Reason.MALFORMED, "a %-escape is not two hex digits")
                raw.write(Character.digit(a, 16) * 16 + Character.digit(b, 16))
                i += 3
                continue
            }
            if (ch !in RAW_OK) throw Refused(Reason.MALFORMED, "the character '$ch' must be percent-encoded")
            raw.write(ch.code)
            i += 1
        }
        return try {
            Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(raw.toByteArray()))
                .toString()
        } catch (_: CharacterCodingException) {
            throw Refused(Reason.MALFORMED, "a value is not UTF-8")
        }
    }

    /** Lower-cases A-Z only (Unicode case mapping differs between runtimes). */
    private fun asciiLower(s: String): String = buildString(s.length) {
        for (c in s) append(if (c in 'A'..'Z') c + 32 else c)
    }
}
