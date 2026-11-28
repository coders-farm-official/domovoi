package com.domovoi.app.away

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.security.MessageDigest

/*
 * Away chat's wire, v1 (AWAY-CHAT-WIRE; the house's copy is
 * domovoi/away/wire.py): pure functions, no sockets.
 *
 *   phone -> "DAC1"                               4-byte preface
 *   phone -> frame(Noise IK message 1)            payload: PHONE_HELLO
 *   house -> frame(Noise IK message 2)            payload: HOUSE_HELLO
 *   either-> frame(Noise transport message)       payload: one message
 *
 * A frame is a 2-byte big-endian length (1..65535) and that many bytes. A
 * message is a 6-byte header (kind u8, req u32 BE, flags u8: bit 0 MORE)
 * and a body.
 */
object AwayWire {
    val PREFACE = "DAC1".toByteArray(Charsets.US_ASCII)
    val PROLOGUE = "domovoi-away-v1".toByteArray(Charsets.US_ASCII)
    const val PROTO = 1
    const val PLATFORM_ANDROID = 1

    const val MAX_FRAME = 65535
    const val MAX_PLAINTEXT = MAX_FRAME - NOISE_TAG
    const val HEADER_SIZE = 6
    const val MAX_BODY = MAX_PLAINTEXT - HEADER_SIZE
    const val FLAG_MORE = 0x01

    // Phone -> house.
    const val REQ = 0x01
    const val BLOB = 0x02
    const val PING = 0x03
    const val BYE = 0x04

    // House -> phone.
    const val RESP = 0x81
    const val EVENT = 0x82
    const val ERR = 0x83
    const val PONG = 0x84
    const val HOUSE_BYE = 0x85

    /** A REQ's JSON, all its pieces together. */
    const val MAX_REQ = 160 * 1024

    /** A RESP's JSON, all its pieces together. */
    const val MAX_RESP = 1024 * 1024

    /** One BLOB's picture bytes. */
    const val MAX_BLOB_PIECE = 65_000

    const val MAX_PICTURE = 1_048_576
    const val MAX_TEXT = 32_000
    const val MAX_LIMIT = 50
    const val MAX_VERSION = 32

    /** Open requests on one connection (§4.6). */
    const val OPEN_REQUESTS = 4

    /** The phone's PING while the chat is open and it has said nothing else (§2.4). */
    const val PING_EVERY_MS = 45_000L

    private val VERSION_CHARS = Regex("^[0-9A-Za-z.+\\-]*$")

    /** PHONE_HELLO: proto u16, platform u8, ver_len u8, version (4..36
     *  bytes). A version outside `[0-9A-Za-z.+-]`, or longer than 32, is
     *  cut to what fits ("1.5.0-test" stays). */
    fun phoneHello(version: String, platform: Int = PLATFORM_ANDROID): ByteArray {
        val v = version.filter { VERSION_CHARS.matches(it.toString()) }.take(MAX_VERSION).toByteArray(Charsets.US_ASCII)
        return byteArrayOf((PROTO ushr 8).toByte(), PROTO.toByte(), platform.toByte(), v.size.toByte()) + v
    }

    /** HOUSE_HELLO's (proto, max_picture); null for any other shape. */
    fun parseHouseHello(data: ByteArray): Pair<Int, Long>? {
        if (data.size != 6) return null
        val proto = u16(data, 0)
        val max = u32(data, 2)
        return proto to max
    }

    fun frame(data: ByteArray): ByteArray {
        require(data.size in 1..MAX_FRAME) { "a frame is 1..65535 bytes" }
        return byteArrayOf((data.size ushr 8).toByte(), data.size.toByte()) + data
    }

    fun header(kind: Int, req: Long, more: Boolean = false): ByteArray = byteArrayOf(
        kind.toByte(),
        (req ushr 24).toByte(), (req ushr 16).toByte(), (req ushr 8).toByte(), req.toByte(),
        (if (more) FLAG_MORE else 0).toByte(),
    )

    fun message(kind: Int, req: Long, body: ByteArray = ByteArray(0), more: Boolean = false): ByteArray {
        require(req in 0..0xFFFF_FFFFL) { "req is a u32" }
        val out = header(kind, req, more) + body
        require(out.size <= MAX_PLAINTEXT) { "message too large" }
        return out
    }

    /** A REQ's JSON as one message, or several with MORE when it is longer
     *  than one transport message holds. Over [MAX_REQ] in all it is not
     *  sent at all ([TooLarge]). */
    fun requestMessages(req: Long, json: ByteArray, room: Int = MAX_BODY): List<ByteArray> {
        if (json.isEmpty()) throw IllegalArgumentException("an empty request")
        if (json.size > MAX_REQ) throw TooLarge()
        val pieces = (json.indices step room).map { json.copyOfRange(it, minOf(it + room, json.size)) }
        return pieces.mapIndexed { i, p -> message(REQ, req, p, more = i < pieces.size - 1) }
    }

    /** A picture as BLOB bodies: `offset u32 BE` then at most
     *  [MAX_BLOB_PIECE] bytes, offsets in order from 0. */
    fun blobBodies(picture: ByteArray, piece: Int = MAX_BLOB_PIECE): List<ByteArray> =
        (picture.indices step piece).map { off ->
            byteArrayOf((off ushr 24).toByte(), (off ushr 16).toByte(), (off ushr 8).toByte(), off.toByte()) +
                picture.copyOfRange(off, minOf(off + piece, picture.size))
        }

    /** One house message (a decrypted transport payload), its header
     *  checked: a known house kind and flags with only MORE; null otherwise
     *  (the connection then closes). */
    fun parseHouse(plain: ByteArray): HouseMessage? {
        if (plain.size < HEADER_SIZE) return null
        val kind = plain[0].toInt() and 0xFF
        val req = u32(plain, 1)
        val flags = plain[5].toInt() and 0xFF
        if (kind !in HOUSE_KINDS || flags and FLAG_MORE.inv() != 0) return null
        return HouseMessage(kind, req, flags and FLAG_MORE != 0, plain.copyOfRange(HEADER_SIZE, plain.size))
    }

    private val HOUSE_KINDS = setOf(RESP, EVENT, ERR, PONG, HOUSE_BYE)

    private fun u16(b: ByteArray, at: Int) = ((b[at].toInt() and 0xFF) shl 8) or (b[at + 1].toInt() and 0xFF)

    private fun u32(b: ByteArray, at: Int): Long =
        ((b[at].toLong() and 0xFF) shl 24) or ((b[at + 1].toLong() and 0xFF) shl 16) or
            ((b[at + 2].toLong() and 0xFF) shl 8) or (b[at + 3].toLong() and 0xFF)

    /** The house's JSON: unknown keys are ignored (later houses may add
     *  fields within proto 1). */
    val json = Json { ignoreUnknownKeys = true; explicitNulls = false; coerceInputValues = true }

    fun parseObject(body: ByteArray): JsonObject? =
        runCatching { json.parseToJsonElement(body.toString(Charsets.UTF_8)) as? JsonObject }.getOrNull()

    // ── The request set (§4) ───────────────────────────────────────────

    fun list(archived: Boolean = false, limit: Int = MAX_LIMIT, cursor: String? = null): JsonObject = buildJsonObject {
        put("op", "chat.list")
        put("archived", archived)
        put("limit", limit.coerceIn(1, MAX_LIMIT))
        put("cursor", cursor?.let(::JsonPrimitive) ?: JsonNull)
    }

    fun history(chatId: Long, beforeId: Long? = null, limit: Int = MAX_LIMIT): JsonObject = buildJsonObject {
        put("op", "chat.history")
        put("chat_id", chatId)
        put("before_id", beforeId?.let(::JsonPrimitive) ?: JsonNull)
        put("limit", limit.coerceIn(1, MAX_LIMIT))
    }

    fun send(chatId: Long?, clientId: String, text: String, picture: PictureInfo? = null, adminGrant: String? = null): JsonObject =
        buildJsonObject {
            put("op", "chat.send")
            put("chat_id", chatId?.let(::JsonPrimitive) ?: JsonNull)
            put("client_id", clientId)
            put("text", text)
            if (picture != null) {
                put("picture", buildJsonObject {
                    put("type", picture.type)
                    put("size", picture.size)
                    put("sha256", picture.sha256)
                })
            }
            if (adminGrant != null) put("admin_grant", adminGrant)
        }

    fun stream(chatId: Long): JsonObject = buildJsonObject { put("op", "chat.stream"); put("chat_id", chatId) }

    fun cancel(chatId: Long): JsonObject = buildJsonObject { put("op", "chat.cancel"); put("chat_id", chatId) }

    fun encode(obj: JsonElement): ByteArray = obj.toString().toByteArray(Charsets.UTF_8)

    /** A new `client_id`: a lower-case UUID. */
    fun newClientId(): String = java.util.UUID.randomUUID().toString().lowercase()

    fun sha256Hex(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }

    /** The number of code points in [text] (the house's 32,000 cap counts those). */
    fun codePoints(text: String): Int = text.codePointCount(0, text.length)
}

/** A picture's declaration in `chat.send`. */
data class PictureInfo(val type: String, val size: Int, val sha256: String) {
    companion object {
        fun jpeg(bytes: ByteArray) = PictureInfo("jpeg", bytes.size, AwayWire.sha256Hex(bytes))
    }
}

/** One message from the house. */
class HouseMessage(val kind: Int, val req: Long, val more: Boolean, val body: ByteArray)

/** A request too large to send (over 160 KiB of JSON). */
class TooLarge : Exception("the request is too large")
