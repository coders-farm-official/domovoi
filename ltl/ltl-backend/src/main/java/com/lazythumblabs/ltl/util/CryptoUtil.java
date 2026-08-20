package com.lazythumblabs.ltl.util;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.AlgorithmParameters;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.ECFieldFp;
import java.security.spec.ECParameterSpec;
import java.security.spec.ECPoint;
import java.security.spec.ECPublicKeySpec;
import java.security.spec.EllipticCurve;
import java.util.Base64;
import java.util.HexFormat;

/**
 * The relay's half of the shared cryptographic vocabulary.
 *
 * <p>Every constant and every byte order here must match
 * {@code plugin-ltl-remote/domovoi_plugin_ltl_remote/crypto.py} exactly.
 * The Python side has the canonical description in
 * {@code ltl/docs/PROTOCOL.md}; this class implements the two pieces the
 * relay actually needs and nothing more:
 *
 * <ul>
 *   <li>verifying an agent's ECDSA signature over a connection challenge
 *       (PROTOCOL.md §8), and</li>
 *   <li>recomputing a household fingerprint from its two public keys, so
 *       the value stored is one LTL derived rather than one a client
 *       asserted (PROTOCOL.md §7).</li>
 * </ul>
 *
 * <p>Conspicuously absent: anything that would let the relay open a
 * frame. There is no ECDH here, no HKDF, and no AES — not because they
 * were forgotten, but because the relay has no business possessing them.
 * If a future change needs one of those on this side, that is a design
 * discussion, not an import.
 *
 * <p>Pure JCA, no BouncyCastle: P-256, SHA-256 and {@code
 * SHA256withECDSA} are all in the platform, and a security dependency
 * avoided is a security dependency that cannot go stale.
 */
public final class CryptoUtil {

    private static final byte[] FINGERPRINT_LABEL =
            "ltl-remote/v1 fp".getBytes(StandardCharsets.UTF_8);
    private static final int FINGERPRINT_BYTES = 16;
    private static final int UNCOMPRESSED_POINT_LENGTH = 65;
    private static final String CURVE = "secp256r1";

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Base64.Decoder URL_DECODER = Base64.getUrlDecoder();
    private static final Base64.Encoder URL_ENCODER = Base64.getUrlEncoder().withoutPadding();

    private CryptoUtil() {}

    /** Thrown for any malformed key or digest input. Never surfaced to a peer verbatim. */
    public static class CryptoException extends RuntimeException {
        public CryptoException(String message) {
            super(message);
        }

        public CryptoException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    // ── encoding ────────────────────────────────────────────────────────

    /** Decode unpadded base64url, the encoding used everywhere in this protocol. */
    public static byte[] decodeBase64Url(String value) {
        if (value == null || value.isEmpty()) {
            throw new CryptoException("expected a base64url string");
        }
        try {
            return URL_DECODER.decode(value);
        } catch (IllegalArgumentException e) {
            throw new CryptoException("malformed base64url", e);
        }
    }

    public static String encodeBase64Url(byte[] raw) {
        return URL_ENCODER.encodeToString(raw);
    }

    // ── digests and tokens ──────────────────────────────────────────────

    public static String sha256Hex(String value) {
        return sha256Hex(value.getBytes(StandardCharsets.UTF_8));
    }

    public static String sha256Hex(byte[] value) {
        return HexFormat.of().formatHex(digest(value));
    }

    private static byte[] digest(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    /**
     * A 256-bit bearer token, base64url. Used for relay tokens and
     * enrollment poll tokens — both of which are stored only as a
     * SHA-256 hash, so this value is shown exactly once and then is gone.
     */
    public static String randomToken() {
        byte[] raw = new byte[32];
        RANDOM.nextBytes(raw);
        return encodeBase64Url(raw);
    }

    /** A short opaque public identifier, e.g. {@code h_} or {@code d_} prefixed. */
    public static String randomUid(String prefix) {
        byte[] raw = new byte[16];
        RANDOM.nextBytes(raw);
        return prefix + HexFormat.of().formatHex(raw);
    }

    /** Constant-time comparison, for anything derived from a secret. */
    public static boolean constantTimeEquals(String a, String b) {
        if (a == null || b == null) {
            return false;
        }
        return MessageDigest.isEqual(
                a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }

    // ── fingerprints (PROTOCOL.md §7) ───────────────────────────────────

    /**
     * Recompute the household fingerprint from its two public keys.
     *
     * <p>Recomputed rather than trusted: an enrolling server sends a
     * fingerprint, but the value LTL stores and shows to a customer must
     * be one derived from the keys it actually holds, or the string
     * people are asked to compare would be attacker-chosen.
     */
    public static String fingerprint(String dhPublicKeyB64, String sigPublicKeyB64) {
        byte[] dh = decodeBase64Url(dhPublicKeyB64);
        byte[] sig = decodeBase64Url(sigPublicKeyB64);
        requireUncompressedPoint(dh, "dh_public_key");
        requireUncompressedPoint(sig, "sig_public_key");

        byte[] input = new byte[FINGERPRINT_LABEL.length + dh.length + sig.length];
        System.arraycopy(FINGERPRINT_LABEL, 0, input, 0, FINGERPRINT_LABEL.length);
        System.arraycopy(dh, 0, input, FINGERPRINT_LABEL.length, dh.length);
        System.arraycopy(sig, 0, input, FINGERPRINT_LABEL.length + dh.length, sig.length);

        String hex = HexFormat.of()
                .formatHex(digest(input), 0, FINGERPRINT_BYTES)
                .toUpperCase();
        StringBuilder grouped = new StringBuilder(hex.length() + 7);
        for (int i = 0; i < hex.length(); i += 4) {
            if (i > 0) {
                grouped.append(' ');
            }
            grouped.append(hex, i, i + 4);
        }
        return grouped.toString();
    }

    /** The shorter, four-group form used for client devices. */
    public static String deviceFingerprint(String publicKeyB64) {
        byte[] key = decodeBase64Url(publicKeyB64);
        requireUncompressedPoint(key, "public_key");
        byte[] label = "ltl-remote/v1 device".getBytes(StandardCharsets.UTF_8);
        byte[] input = new byte[label.length + key.length];
        System.arraycopy(label, 0, input, 0, label.length);
        System.arraycopy(key, 0, input, label.length, key.length);

        String hex = HexFormat.of().formatHex(digest(input)).substring(0, 16).toUpperCase();
        StringBuilder grouped = new StringBuilder(19);
        for (int i = 0; i < hex.length(); i += 4) {
            if (i > 0) {
                grouped.append(' ');
            }
            grouped.append(hex, i, i + 4);
        }
        return grouped.toString();
    }

    // ── agent authentication (PROTOCOL.md §8) ───────────────────────────

    /**
     * Verify {@code ECDSA-P256-SHA256(challenge || householdUid)} against
     * a household's stored signing key.
     *
     * <p>The household id is inside the signed blob so a signature
     * captured from one household cannot be replayed to claim another's
     * agent slot. Returns false rather than throwing: at this point in a
     * connection every failure means the same thing — close it.
     */
    public static boolean verifyChallenge(
            String sigPublicKeyB64, byte[] challenge, String householdUid, String signatureB64) {
        try {
            byte[] payload = concat(challenge, householdUid.getBytes(StandardCharsets.UTF_8));
            Signature verifier = Signature.getInstance("SHA256withECDSA");
            verifier.initVerify(parsePublicKey(sigPublicKeyB64));
            verifier.update(payload);
            return verifier.verify(decodeBase64Url(signatureB64));
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * Parse an uncompressed SEC1 P-256 point into a {@link PublicKey}.
     *
     * <p>Includes an explicit on-curve check, which is <em>not</em>
     * redundant: the JDK's {@code KeyFactory.generatePublic} happily
     * builds an {@code ECPublicKey} from coordinates that do not satisfy
     * the curve equation. Python's {@code from_encoded_point} rejects
     * those, so without this the two implementations would disagree
     * about which keys are acceptable — and, worse, the relay would
     * store attacker-chosen points as household identities.
     *
     * <p>The curve parameters come from the JDK's own P-256 spec rather
     * than being written out here, so there is no second copy of the
     * constants to get wrong.
     */
    public static PublicKey parsePublicKey(String publicKeyB64) {
        byte[] raw = decodeBase64Url(publicKeyB64);
        requireUncompressedPoint(raw, "public key");
        try {
            byte[] x = new byte[32];
            byte[] y = new byte[32];
            System.arraycopy(raw, 1, x, 0, 32);
            System.arraycopy(raw, 33, y, 0, 32);

            AlgorithmParameters parameters = AlgorithmParameters.getInstance("EC");
            parameters.init(new java.security.spec.ECGenParameterSpec(CURVE));
            ECParameterSpec spec = parameters.getParameterSpec(ECParameterSpec.class);

            ECPoint point = new ECPoint(new BigInteger(1, x), new BigInteger(1, y));
            requireOnCurve(point, spec.getCurve());
            return KeyFactory.getInstance("EC").generatePublic(new ECPublicKeySpec(point, spec));
        } catch (CryptoException e) {
            throw e;
        } catch (Exception e) {
            throw new CryptoException("not a valid P-256 public key", e);
        }
    }

    /**
     * Check that a point actually lies on the curve: y² ≡ x³ + ax + b
     * (mod p), with both coordinates in range and the point not at
     * infinity.
     *
     * <p>This is the invalid-curve defense. Feeding a carefully chosen
     * off-curve point into an ECDH is a known way to extract a private
     * scalar one exchange at a time; the relay does no ECDH, but it
     * hands these keys to households that do.
     */
    private static void requireOnCurve(ECPoint point, EllipticCurve curve) {
        if (point.equals(ECPoint.POINT_INFINITY)) {
            throw new CryptoException("public key is the point at infinity");
        }
        if (!(curve.getField() instanceof ECFieldFp field)) {
            throw new CryptoException("unexpected curve field");
        }
        BigInteger p = field.getP();
        BigInteger x = point.getAffineX();
        BigInteger y = point.getAffineY();
        if (x.signum() < 0 || x.compareTo(p) >= 0 || y.signum() < 0 || y.compareTo(p) >= 0) {
            throw new CryptoException("public key coordinates are out of range");
        }
        BigInteger left = y.modPow(BigInteger.TWO, p);
        BigInteger right = x.modPow(BigInteger.valueOf(3), p)
                .add(curve.getA().multiply(x))
                .add(curve.getB())
                .mod(p);
        if (!left.equals(right)) {
            throw new CryptoException("public key is not a point on P-256");
        }
    }

    /** True if the value parses as a P-256 point on the curve. */
    public static boolean isValidPublicKey(String publicKeyB64) {
        try {
            parsePublicKey(publicKeyB64);
            return true;
        } catch (RuntimeException e) {
            return false;
        }
    }

    private static void requireUncompressedPoint(byte[] raw, String label) {
        if (raw.length != UNCOMPRESSED_POINT_LENGTH || raw[0] != 0x04) {
            throw new CryptoException(
                    label + " must be a 65-byte uncompressed SEC1 point");
        }
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }
}
