package com.domovoi.app.net

import java.math.BigInteger
import java.security.MessageDigest

/**
 * Ed25519 (RFC 8032) signature VERIFICATION in plain Kotlin over
 * `java.math.BigInteger` — a line-for-line port of the verify half of the
 * core's vendored `domovoi/_ed25519.py`, which is what signs when the core
 * has no `cryptography`; the two produce and accept identical signatures
 * (the RFC 8032 vectors pin both to the standard).
 *
 * Why not the platform: Android's providers only grew Ed25519 in recent
 * releases and the app runs from API 26; why not a library: the build is
 * offline-reproducible from a fixed dependency set and this is forty lines
 * of field arithmetic. Not constant-time — verification touches public
 * data only, and this phone never signs.
 */
object Ed25519 {
    const val KEY_SIZE = 32
    const val SIGNATURE_SIZE = 64

    private val TWO = BigInteger.valueOf(2)
    private val EIGHT = BigInteger.valueOf(8)

    /** The field prime 2^255 - 19 and the base point's subgroup order. */
    private val P: BigInteger = TWO.pow(255).subtract(BigInteger.valueOf(19))
    private val L: BigInteger = TWO.pow(252).add(BigInteger("27742317777372353535851937790883648493"))

    private val D: BigInteger = BigInteger.valueOf(-121665).multiply(BigInteger.valueOf(121666).modInverse(P)).mod(P)
    private val SQRT_M1: BigInteger = TWO.modPow(P.subtract(BigInteger.ONE).divide(BigInteger.valueOf(4)), P)

    /** A point in extended coordinates (X : Y : Z : T). */
    private class Point(val x: BigInteger, val y: BigInteger, val z: BigInteger, val t: BigInteger)

    private val NEUTRAL = Point(BigInteger.ZERO, BigInteger.ONE, BigInteger.ONE, BigInteger.ZERO)

    private val BASE: Point = run {
        val y = BigInteger.valueOf(4).multiply(BigInteger.valueOf(5).modInverse(P)).mod(P)
        val x = recoverX(y, 0) ?: error("the base point is on the curve")
        Point(x, y, BigInteger.ONE, x.multiply(y).mod(P))
    }

    /**
     * Whether [signature] is [publicKey]'s signature over [message]. Never
     * throws: a malformed key, a malformed signature and a wrong signature
     * are all the same answer to the caller — no.
     */
    fun verify(publicKey: ByteArray, message: ByteArray, signature: ByteArray): Boolean {
        if (publicKey.size != KEY_SIZE || signature.size != SIGNATURE_SIZE) return false
        val a = decodePoint(publicKey) ?: return false
        val encodedR = signature.copyOfRange(0, 32)
        val r = decodePoint(encodedR) ?: return false
        val s = littleEndian(signature.copyOfRange(32, 64))
        if (s >= L) return false
        val k = littleEndian(sha512(encodedR + publicKey + message)).mod(L)
        val lhs = scalarMult(BASE, s)
        val rhs = add(r, scalarMult(a, k))
        return encodePoint(lhs).contentEquals(encodePoint(rhs))
    }

    /**
     * TESTS ONLY: the signing half, so a fake server in a unit test can
     * answer a fresh challenge the way the core does. No key ever exists on
     * the phone; the app never calls this. Same port of the core's
     * `_ed25519.sign`.
     */
    internal fun publicKey(seed: ByteArray): ByteArray {
        require(seed.size == KEY_SIZE) { "an ed25519 private seed is 32 bytes" }
        return encodePoint(scalarMult(BASE, expand(seed).first))
    }

    internal fun sign(seed: ByteArray, message: ByteArray): ByteArray {
        require(seed.size == KEY_SIZE) { "an ed25519 private seed is 32 bytes" }
        val (a, prefix) = expand(seed)
        val encodedA = encodePoint(scalarMult(BASE, a))
        val r = littleEndian(sha512(prefix + message)).mod(L)
        val encodedR = encodePoint(scalarMult(BASE, r))
        val k = littleEndian(sha512(encodedR + encodedA + message)).mod(L)
        val s = r.add(k.multiply(a)).mod(L)
        return encodedR + toLittleEndian(s, 32)
    }

    private fun expand(seed: ByteArray): Pair<BigInteger, ByteArray> {
        val h = sha512(seed)
        var a = littleEndian(h.copyOfRange(0, 32))
        a = a.and(TWO.pow(254).subtract(EIGHT))     // clear the low three bits and bit 255
        a = a.setBit(254)
        return a to h.copyOfRange(32, 64)
    }

    // ---- the arithmetic ---------------------------------------------------

    /** The x that goes with [y] on the curve, with the requested parity,
     *  or null when [y] is not on the curve at all. */
    private fun recoverX(y: BigInteger, sign: Int): BigInteger? {
        if (y >= P) return null
        val y2 = y.multiply(y).mod(P)
        val xx = y2.subtract(BigInteger.ONE).multiply(D.multiply(y2).add(BigInteger.ONE).modInverse(P)).mod(P)
        var x = xx.modPow(P.add(BigInteger.valueOf(3)).divide(EIGHT), P)
        if (x.multiply(x).subtract(xx).mod(P).signum() != 0) x = x.multiply(SQRT_M1).mod(P)
        if (x.multiply(x).subtract(xx).mod(P).signum() != 0) return null
        if (x.signum() == 0 && sign == 1) return null
        if (x.testBit(0) != (sign == 1)) x = P.subtract(x)
        return x
    }

    /** Unified extended-coordinate addition (add-2008-hwcd-3, a = -1):
     *  correct for doubling too, so scalar multiplication needs one formula. */
    private fun add(p1: Point, p2: Point): Point {
        val a = p1.y.subtract(p1.x).multiply(p2.y.subtract(p2.x)).mod(P)
        val b = p1.y.add(p1.x).multiply(p2.y.add(p2.x)).mod(P)
        val c = TWO.multiply(p1.t).multiply(p2.t).multiply(D).mod(P)
        val dd = TWO.multiply(p1.z).multiply(p2.z).mod(P)
        val e = b.subtract(a)
        val f = dd.subtract(c)
        val g = dd.add(c)
        val h = b.add(a)
        return Point(e.multiply(f).mod(P), g.multiply(h).mod(P), f.multiply(g).mod(P), e.multiply(h).mod(P))
    }

    private fun scalarMult(point: Point, e: BigInteger): Point {
        var result = NEUTRAL
        var p = point
        var k = e
        while (k.signum() > 0) {
            if (k.testBit(0)) result = add(result, p)
            p = add(p, p)
            k = k.shiftRight(1)
        }
        return result
    }

    private fun encodePoint(point: Point): ByteArray {
        val zi = point.z.modInverse(P)
        val x = point.x.multiply(zi).mod(P)
        val y = point.y.multiply(zi).mod(P)
        val value = if (x.testBit(0)) y.setBit(255) else y
        return toLittleEndian(value, 32)
    }

    private fun decodePoint(data: ByteArray): Point? {
        if (data.size != KEY_SIZE) return null
        val value = littleEndian(data)
        val sign = if (value.testBit(255)) 1 else 0
        val y = value.clearBit(255)
        val x = recoverX(y, sign) ?: return null
        return Point(x, y, BigInteger.ONE, x.multiply(y).mod(P))
    }

    private fun littleEndian(bytes: ByteArray): BigInteger = BigInteger(1, bytes.reversedArray())

    private fun toLittleEndian(value: BigInteger, size: Int): ByteArray {
        val big = value.toByteArray()            // big-endian, maybe with a sign byte
        val out = ByteArray(size)
        var i = big.size - 1
        var o = 0
        while (i >= 0 && o < size) {
            out[o++] = big[i--]
        }
        return out
    }

    private fun sha512(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-512").digest(data)
}
