package com.domovoi.app.away

import org.bouncycastle.crypto.InvalidCipherTextException
import org.bouncycastle.crypto.modes.ChaCha20Poly1305
import org.bouncycastle.crypto.params.AEADParameters
import org.bouncycastle.crypto.params.KeyParameter
import org.bouncycastle.math.ec.rfc7748.X25519
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/*
 * Noise_IK_25519_ChaChaPoly_SHA256, the one pattern away chat speaks
 * (AWAY-CHAT-WIRE §2.2), written out from the Noise Protocol Framework
 * (revision 34) the way the house's own copy is (domovoi/friend_houses/
 * noise.py): the phone is the initiator, with its Noise static key as `s`,
 * and knows the house's away key (`house_noise_pub`) before it starts.
 * Both sides end up authenticated, with fresh ephemeral keys.
 *
 * The primitives come from Bouncy Castle's lightweight API (MIT licence):
 * X25519 and ChaCha20-Poly1305, constant time, which Android's own
 * providers only have from Android 13 (X25519) and 9 (ChaCha20-Poly1305),
 * while the app runs from Android 8. SHA-256 and HMAC are the platform's.
 * The responder half exists for the cacophony vectors and the tests'
 * loopback house; the app only ever starts handshakes.
 *
 * Checked against the published cacophony vectors the house is checked
 * against (domovoi/tests/fixtures/noise_vectors.json, AwayNoiseTest) and
 * against the house's own Python responder (AwayLoopbackE2ETest).
 */

/** A handshake or transport message was refused. Never says why in a way
 *  a peer can use: the connection is dropped. */
class NoiseException(message: String) : Exception(message)

object Curve25519 {
    const val KEY = 32

    /** A fresh private key (clamped, as X25519 uses it). */
    fun newPrivate(random: SecureRandom = SecureRandom()): ByteArray =
        ByteArray(KEY).also { X25519.generatePrivateKey(random, it) }

    fun publicOf(private: ByteArray): ByteArray {
        require(private.size == KEY) { "an X25519 key is 32 bytes" }
        return ByteArray(KEY).also { X25519.generatePublicKey(private, 0, it, 0) }
    }

    /** The shared secret; a low-order point (an all-zero result) is refused. */
    fun dh(private: ByteArray, public: ByteArray): ByteArray {
        if (private.size != KEY || public.size != KEY) throw NoiseException("bad key")
        val out = ByteArray(KEY)
        if (!X25519.calculateAgreement(private, 0, public, 0, out, 0)) throw NoiseException("bad key")
        return out
    }
}

private const val HASHLEN = 32
internal const val NOISE_TAG = 16
internal const val NOISE_MAX_MESSAGE = 65535

private fun sha256(vararg parts: ByteArray): ByteArray =
    MessageDigest.getInstance("SHA-256").run { parts.forEach { update(it) }; digest() }

private fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray =
    Mac.getInstance("HmacSHA256").run {
        init(SecretKeySpec(key, "HmacSHA256"))
        parts.forEach { update(it) }
        doFinal()
    }

private fun hkdf(chainingKey: ByteArray, ikm: ByteArray, outputs: Int): List<ByteArray> {
    val temp = hmac(chainingKey, ikm)
    val o1 = hmac(temp, byteArrayOf(1))
    val o2 = hmac(temp, o1, byteArrayOf(2))
    return if (outputs == 2) listOf(o1, o2) else listOf(o1, o2, hmac(temp, o2, byteArrayOf(3)))
}

/** One direction of an encrypted channel: a key and a message counter. */
class NoiseCipher internal constructor(private val key: ByteArray?) {
    private var nonce = 0L

    val hasKey: Boolean get() = key != null

    private fun nonceBytes(n: Long): ByteArray = ByteArray(12).also { b ->
        for (i in 0 until 8) b[4 + i] = (n ushr (8 * i)).toByte()
    }

    private fun aead(encrypt: Boolean, ad: ByteArray, input: ByteArray): ByteArray {
        val c = ChaCha20Poly1305()
        c.init(encrypt, AEADParameters(KeyParameter(key), NOISE_TAG * 8, nonceBytes(nonce), ad))
        val out = ByteArray(c.getOutputSize(input.size))
        var n = c.processBytes(input, 0, input.size, out, 0)
        n += c.doFinal(out, n)
        return if (n == out.size) out else out.copyOf(n)
    }

    fun encrypt(ad: ByteArray, plaintext: ByteArray): ByteArray {
        key ?: return plaintext
        if (nonce == -1L) throw NoiseException("channel exhausted")
        return aead(true, ad, plaintext).also { nonce++ }
    }

    fun decrypt(ad: ByteArray, ciphertext: ByteArray): ByteArray {
        key ?: return ciphertext
        if (nonce == -1L) throw NoiseException("channel exhausted")
        val plain = try {
            aead(false, ad, ciphertext)
        } catch (e: InvalidCipherTextException) {
            throw NoiseException("message refused")
        } catch (e: IllegalArgumentException) {
            throw NoiseException("message refused")
        }
        nonce++ // only after a successful decrypt (spec 5.1)
        return plain
    }
}

private class SymmetricState(protocolName: ByteArray) {
    var h: ByteArray = if (protocolName.size <= HASHLEN) protocolName.copyOf(HASHLEN) else sha256(protocolName)
    var ck: ByteArray = h.copyOf()
    var cipher = NoiseCipher(null)

    fun mixKey(ikm: ByteArray) {
        val (ck2, k) = hkdf(ck, ikm, 2)
        ck = ck2
        cipher = NoiseCipher(k)
    }

    fun mixHash(data: ByteArray) {
        h = sha256(h, data)
    }

    fun encryptAndHash(plain: ByteArray): ByteArray = cipher.encrypt(h, plain).also { mixHash(it) }

    fun decryptAndHash(ct: ByteArray): ByteArray = cipher.decrypt(h, ct).also { mixHash(ct) }

    fun split(): Pair<NoiseCipher, NoiseCipher> {
        val (k1, k2) = hkdf(ck, ByteArray(0), 2)
        return NoiseCipher(k1) to NoiseCipher(k2)
    }
}

/**
 * One side of a Noise_IK_25519_ChaChaPoly_SHA256 handshake. The initiator
 * passes the responder's static key ([remoteStatic]); the responder learns
 * the initiator's from the first message. [ephemeral] is for the test
 * vectors only: a real handshake makes a fresh one.
 */
class NoiseIK(
    val initiator: Boolean,
    prologue: ByteArray,
    private val static: ByteArray,
    remoteStatic: ByteArray? = null,
    private var ephemeral: ByteArray? = null,
) {
    private val ss = SymmetricState(PROTOCOL.toByteArray(Charsets.US_ASCII))
    private var rs: ByteArray? = if (initiator) remoteStatic else null
    private var re: ByteArray? = null
    private var step = 0
    private var result: Pair<NoiseCipher, NoiseCipher>? = null

    init {
        require(static.size == Curve25519.KEY) { "the static key is 32 bytes" }
        if (initiator) require(remoteStatic?.size == Curve25519.KEY) { "the initiator must know the responder's key" }
        ss.mixHash(prologue)
        // Pre-message pattern:  <- s
        ss.mixHash(if (initiator) remoteStatic!! else Curve25519.publicOf(static))
    }

    val complete: Boolean get() = result != null

    /** The other side's static key (the responder knows it after message 1). */
    val remoteStaticKey: ByteArray? get() = rs?.copyOf()

    val handshakeHash: ByteArray get() = ss.h.copyOf()

    private fun myTurn() = (step % 2 == 0) == initiator

    private fun dh(token: String): ByteArray {
        val e = ephemeral
        return when (token) {
            "ee" -> Curve25519.dh(e!!, re!!)
            "ss" -> Curve25519.dh(static, rs!!)
            // The initiator's ephemeral with the responder's static.
            "es" -> if (initiator) Curve25519.dh(e!!, rs!!) else Curve25519.dh(static, re!!)
            // "se": the initiator's static with the responder's ephemeral.
            else -> if (initiator) Curve25519.dh(static, re!!) else Curve25519.dh(e!!, rs!!)
        }
    }

    private fun finishStep() {
        step++
        if (step == MESSAGES.size) result = ss.split()
    }

    fun writeMessage(payload: ByteArray = ByteArray(0)): ByteArray {
        if (complete || !myTurn()) throw NoiseException("out of turn")
        val out = java.io.ByteArrayOutputStream()
        for (token in MESSAGES[step]) {
            when (token) {
                "e" -> {
                    val e = ephemeral ?: Curve25519.newPrivate().also { ephemeral = it }
                    val pub = Curve25519.publicOf(e)
                    out.write(pub)
                    ss.mixHash(pub)
                }
                "s" -> out.write(ss.encryptAndHash(Curve25519.publicOf(static)))
                else -> ss.mixKey(dh(token))
            }
        }
        out.write(ss.encryptAndHash(payload))
        if (out.size() > NOISE_MAX_MESSAGE) throw NoiseException("message too large")
        finishStep()
        return out.toByteArray()
    }

    fun readMessage(message: ByteArray): ByteArray {
        if (complete || myTurn()) throw NoiseException("out of turn")
        if (message.size > NOISE_MAX_MESSAGE) throw NoiseException("message too large")
        var pos = 0
        for (token in MESSAGES[step]) {
            when (token) {
                "e" -> {
                    if (message.size - pos < Curve25519.KEY) throw NoiseException("message too short")
                    re = message.copyOfRange(pos, pos + Curve25519.KEY).also { ss.mixHash(it) }
                    pos += Curve25519.KEY
                }
                "s" -> {
                    val size = Curve25519.KEY + if (ss.cipher.hasKey) NOISE_TAG else 0
                    if (message.size - pos < size) throw NoiseException("message too short")
                    rs = ss.decryptAndHash(message.copyOfRange(pos, pos + size))
                    pos += size
                }
                else -> ss.mixKey(dh(token))
            }
        }
        val payload = ss.decryptAndHash(message.copyOfRange(pos, message.size))
        finishStep()
        return payload
    }

    /** The two encrypted directions, once the handshake is complete. */
    fun transport(): NoiseTransport {
        val (c1, c2) = result ?: throw NoiseException("handshake not complete")
        return if (initiator) NoiseTransport(c1, c2) else NoiseTransport(c2, c1)
    }

    companion object {
        const val PROTOCOL = "Noise_IK_25519_ChaChaPoly_SHA256"
        private val MESSAGES = listOf(listOf("e", "es", "s", "ss"), listOf("e", "ee", "se"))
    }
}

/** The encrypted channel after the handshake: one counter per direction.
 *  Not thread-safe: the connection encrypts and writes under one lock, and
 *  reads on one reader. */
class NoiseTransport internal constructor(private val send: NoiseCipher, private val recv: NoiseCipher) {
    fun encrypt(plaintext: ByteArray): ByteArray {
        if (plaintext.size + NOISE_TAG > NOISE_MAX_MESSAGE) throw NoiseException("message too large")
        return send.encrypt(ByteArray(0), plaintext)
    }

    fun decrypt(ciphertext: ByteArray): ByteArray {
        if (ciphertext.size > NOISE_MAX_MESSAGE) throw NoiseException("message too large")
        return recv.decrypt(ByteArray(0), ciphertext)
    }
}
