package com.domovoi.app.net

import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * The server's cryptographic identity, as this phone checks it.
 *
 * The core has a long-lived Ed25519 key (domovoi/server_identity.py). Its
 * `/v1/health?challenge=<nonce>` answer carries the public key, its
 * fingerprint (`SHA256:<base64 of sha256(key)>`, the string Settings →
 * About shows and a prepared satellite is baked with) and a signature over
 * `domovoi-health-v1\n<nonce>`; the web backend passes that block through
 * on `/api/health` (security round 3, A6-03). A recording of an earlier
 * answer signs somebody else's nonce and fails.
 *
 * The phone PINS the key the first time it talks to a server it trusts and
 * compares every later answer against it (net/IdentityGate.kt). This file
 * is the pure half: what a proof is, and whether a given answer is one.
 */
object ServerIdentity {
    const val ALGORITHM = "ed25519"
    const val HEALTH_CONTEXT = "domovoi-health-v1"
    const val FINGERPRINT_PREFIX = "SHA256:"

    /** The longest challenge the core accepts (CHALLENGE_MAX_LEN). */
    private const val CHALLENGE_BYTES = 16

    /** A pinned identity: the key (base64) and the fingerprint a person
     *  can read off the dashboard. */
    @Serializable
    data class Pin(val public_key: String, val fingerprint: String)

    /** What an answer proved. */
    sealed class Proof {
        /** Signed by [pin]'s key over our challenge; the pinned key, or the
         *  key to pin when there was none. */
        data class Verified(val pin: Pin) : Proof()

        /** The answer carried no identity block at all: a web backend from
         *  before identity, or one whose core did not answer. */
        data object NoIdentity : Proof()

        /** An identity block that does not hold up: wrong algorithm, a
         *  fingerprint that is not the key's, our challenge not echoed, a
         *  signature that fails. */
        data class Invalid(val reason: String) : Proof()

        /** A valid proof — of a DIFFERENT key than the one pinned. */
        data class Mismatch(val expected: String, val seen: String) : Proof()
    }

    fun newChallenge(): String {
        val bytes = ByteArray(CHALLENGE_BYTES).also { SecureRandom().nextBytes(it) }
        return bytes.joinToString("") { "%02x".format(it) }
    }

    /** `SHA256:<base64 of sha256(key)>`, unpadded — the core's shape. */
    fun fingerprintOf(publicKey: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(publicKey)
        return FINGERPRINT_PREFIX + Base64.getEncoder().withoutPadding().encodeToString(digest)
    }

    /** The bytes the core signs for a health challenge. Must agree with
     *  `server_identity.health_message` byte for byte. */
    fun healthMessage(challenge: String): ByteArray =
        (HEALTH_CONTEXT + "\n" + challenge).toByteArray(Charsets.UTF_8)

    /**
     * Judge a `/api/health` answer ([healthBody], the JSON text) against
     * [challenge] and the key this phone has [pinned] for the server (null
     * when none yet). Never throws.
     */
    fun check(healthBody: String?, challenge: String, pinned: Pin?): Proof {
        val doc = runCatching { DomovoiJson.parseToJsonElement(healthBody.orEmpty()) as? JsonObject }.getOrNull()
            ?: return Proof.Invalid("the health answer is not a JSON object")
        val identity = doc["identity"] as? JsonObject ?: return Proof.NoIdentity
        fun field(name: String): String? = identity[name]?.let { runCatching { it.jsonPrimitive.contentOrNull }.getOrNull() }

        if (field("algorithm") != ALGORITHM) return Proof.Invalid("unknown identity algorithm")
        val keyB64 = field("public_key") ?: return Proof.Invalid("no public key")
        val key = unb64(keyB64)?.takeIf { it.size == Ed25519.KEY_SIZE } ?: return Proof.Invalid("malformed public key")
        val fingerprint = fingerprintOf(key)
        if (field("fingerprint") != fingerprint) return Proof.Invalid("the fingerprint is not the key's")
        if (field("challenge") != challenge) return Proof.Invalid("our challenge was not echoed")
        val signature = field("signature")?.let(::unb64)?.takeIf { it.size == Ed25519.SIGNATURE_SIZE }
            ?: return Proof.Invalid("malformed signature")
        if (!Ed25519.verify(key, healthMessage(challenge), signature)) return Proof.Invalid("the signature does not verify")
        if (pinned != null && pinned.public_key != keyB64) return Proof.Mismatch(pinned.fingerprint, fingerprint)
        return Proof.Verified(Pin(keyB64, fingerprint))
    }

    private fun unb64(value: String): ByteArray? =
        if (value.length > 256) null else runCatching { Base64.getDecoder().decode(value) }.getOrNull()

    // ---- the pin book, one per server --------------------------------------

    private val pinsSerializer = MapSerializer(String.serializer(), Pin.serializer())

    fun encodePins(pins: Map<String, Pin>): String = Json.encodeToString(pinsSerializer, pins)

    /** Never throws: a corrupt or absent blob means "nothing pinned yet". */
    fun decodePins(raw: String?): Map<String, Pin> = runCatching {
        Json.decodeFromString(pinsSerializer, raw ?: "{}")
    }.getOrDefault(emptyMap())
}
