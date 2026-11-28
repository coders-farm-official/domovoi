package com.domovoi.app.away

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.Base64

/*
 * Away chat's keys on this phone (AWAY-CHAT-WIRE §1): two X25519 key pairs,
 * made at home when away chat is set up, the public halves sent to the house
 * with POST /api/away/enroll.
 *
 *  - The Tor client-auth key: the house's away onion is a v3 onion service
 *    with client authorization, and Tor reads the raw private half from an
 *    `.auth_private` file (ArtiTransport). Public half: 52 characters of
 *    upper-case base32, no padding.
 *  - The Noise static key: the phone's `s` in the IK handshake. Public half:
 *    43 characters of base64url, no padding.
 *
 * The private halves are kept this-device-only: sealed under an Android
 * Keystore key of their own, in a file of their own that every backup and
 * transfer leaves out (data/Prefs.kt; res/xml/data_extraction_rules.xml),
 * next to the onion, port, house key and proto the house answered. Wiped
 * when away chat is turned off, the phone unpairs, the server is forgotten,
 * or the server proves a different identity.
 */

/** Everything away chat keeps for one server. The private keys are base64
 *  here and never leave the vault; [toString] never prints them. */
@Serializable
data class AwayEnrolment(
    val torPrivate: String,
    val noisePrivate: String,
    val onion: String,
    val port: Int,
    val houseNoisePub: String,
    val proto: Int,
    /** `ready` / `starting` as enrolment answered (a hint). */
    val state: String? = null,
) {
    fun torPrivateBytes(): ByteArray = Base64.getDecoder().decode(torPrivate)
    fun noisePrivateBytes(): ByteArray = Base64.getDecoder().decode(noisePrivate)
    fun housePublicBytes(): ByteArray? = AwayKeys.unb64url(houseNoisePub, 32)

    /** The onion as Tor's `.auth_private` names it: no `.onion`. */
    val onionHost: String get() = onion.removeSuffix(".onion")

    override fun toString(): String = "AwayEnrolment($onion:$port, proto $proto)"
}

/** A fresh pair of key pairs, before the house has answered. */
class AwayKeyPairs(val torPrivate: ByteArray, val noisePrivate: ByteArray) {
    val torPublic: ByteArray = Curve25519.publicOf(torPrivate)
    val noisePublic: ByteArray = Curve25519.publicOf(noisePrivate)

    /** The enroll body's `tor_auth_pub`. */
    val torAuthPub: String get() = AwayKeys.base32(torPublic)

    /** The enroll body's `noise_pub`. */
    val noisePub: String get() = AwayKeys.b64url(noisePublic)

    override fun toString(): String = "AwayKeyPairs(…)"

    companion object {
        fun generate(random: java.security.SecureRandom = java.security.SecureRandom()): AwayKeyPairs {
            while (true) {
                val k = AwayKeyPairs(Curve25519.newPrivate(random), Curve25519.newPrivate(random))
                // The house refuses two equal public keys (bad_key).
                if (!k.torPublic.contentEquals(k.noisePublic)) return k
            }
        }
    }
}

object AwayKeys {
    private const val B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

    /** RFC 4648 base32, upper case, no padding (Tor's key format). */
    fun base32(data: ByteArray): String {
        val out = StringBuilder()
        var buffer = 0
        var bits = 0
        for (b in data) {
            buffer = (buffer shl 8) or (b.toInt() and 0xFF)
            bits += 8
            while (bits >= 5) {
                out.append(B32[(buffer shr (bits - 5)) and 31])
                bits -= 5
            }
        }
        if (bits > 0) out.append(B32[(buffer shl (5 - bits)) and 31])
        return out.toString()
    }

    fun b64url(raw: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(raw)

    /** [size] bytes from unpadded base64url, or null for any other shape. */
    fun unb64url(text: String?, size: Int): ByteArray? {
        if (text == null || text.length != (size * 4 + 2) / 3) return null
        val raw = runCatching { Base64.getUrlDecoder().decode(text) }.getOrNull() ?: return null
        return raw.takeIf { it.size == size && b64url(it) == text }
    }

    /** Tor's `.auth_private` line for [onionHost] (56 characters, no
     *  `.onion`): `<onion>:descriptor:x25519:<base32 private key>`. */
    fun authPrivateLine(onionHost: String, torPrivate: ByteArray): String =
        "$onionHost:descriptor:x25519:${base32(torPrivate)}"

    /** A v3 onion address: 56 base32 characters and `.onion`. */
    private val ONION = Regex("^[a-z2-7]{56}\\.onion$")

    fun isOnion(address: String): Boolean = ONION.matches(address)

    // ── The vault's record ─────────────────────────────────────────────

    private val json = Json { ignoreUnknownKeys = true }

    /** One server's enrolment as the vault keeps it (a value of its
     *  server-to-record map, data/Prefs.kt). */
    fun encode(e: AwayEnrolment): String = json.encodeToString(AwayEnrolment.serializer(), e)

    fun decode(raw: String?): AwayEnrolment? =
        raw?.let { runCatching { json.decodeFromString(AwayEnrolment.serializer(), it) }.getOrNull() }
}
