package com.lazythumblabs.ltl.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.HexFormat;

import static org.junit.jupiter.api.Assertions.*;

/**
 * The relay's cryptography, checked against the Python implementation's
 * output rather than against itself.
 */
class CryptoUtilTest {

    @Test
    @DisplayName("fingerprints match the Python implementation byte for byte")
    void fingerprintMatchesPython() {
        assertEquals(
                CrossImplementationVectors.FINGERPRINT,
                CryptoUtil.fingerprint(
                        CrossImplementationVectors.DH_PUBLIC_KEY,
                        CrossImplementationVectors.SIG_PUBLIC_KEY));
    }

    @Test
    @DisplayName("device fingerprints match the Python implementation")
    void deviceFingerprintMatchesPython() {
        assertEquals(
                CrossImplementationVectors.DEVICE_FINGERPRINT,
                CryptoUtil.deviceFingerprint(CrossImplementationVectors.DH_PUBLIC_KEY));
    }

    @Test
    @DisplayName("a fingerprint covers BOTH keys, so swapping either changes it")
    void fingerprintCoversBothKeys() {
        String base = CryptoUtil.fingerprint(
                CrossImplementationVectors.DH_PUBLIC_KEY,
                CrossImplementationVectors.SIG_PUBLIC_KEY);
        String swapped = CryptoUtil.fingerprint(
                CrossImplementationVectors.SIG_PUBLIC_KEY,
                CrossImplementationVectors.DH_PUBLIC_KEY);
        assertNotEquals(base, swapped);
    }

    @Test
    @DisplayName("fingerprints render as eight uppercase hex groups of four")
    void fingerprintFormat() {
        String[] groups = CryptoUtil.fingerprint(
                CrossImplementationVectors.DH_PUBLIC_KEY,
                CrossImplementationVectors.SIG_PUBLIC_KEY).split(" ");
        assertEquals(8, groups.length);
        for (String group : groups) {
            assertEquals(4, group.length());
            assertEquals(group.toUpperCase(), group);
        }
    }

    @Test
    @DisplayName("a signature produced by the Python agent verifies here")
    void verifiesPythonSignature() {
        byte[] challenge = CryptoUtil.decodeBase64Url(CrossImplementationVectors.CHALLENGE);
        assertTrue(CryptoUtil.verifyChallenge(
                CrossImplementationVectors.SIG_PUBLIC_KEY,
                challenge,
                CrossImplementationVectors.HOUSEHOLD_UID,
                CrossImplementationVectors.CHALLENGE_SIGNATURE));
    }

    @Test
    @DisplayName("a signature is bound to its household, so it cannot be replayed elsewhere")
    void signatureIsBoundToHousehold() {
        byte[] challenge = CryptoUtil.decodeBase64Url(CrossImplementationVectors.CHALLENGE);
        assertFalse(CryptoUtil.verifyChallenge(
                CrossImplementationVectors.SIG_PUBLIC_KEY,
                challenge,
                "h_someone_elses_household_entirely",
                CrossImplementationVectors.CHALLENGE_SIGNATURE));
    }

    @Test
    @DisplayName("a signature is bound to its challenge, so it cannot be replayed later")
    void signatureIsBoundToChallenge() {
        byte[] other = new byte[32];
        java.util.Arrays.fill(other, (byte) 0x7F);
        assertFalse(CryptoUtil.verifyChallenge(
                CrossImplementationVectors.SIG_PUBLIC_KEY,
                other,
                CrossImplementationVectors.HOUSEHOLD_UID,
                CrossImplementationVectors.CHALLENGE_SIGNATURE));
    }

    @Test
    @DisplayName("the wrong key does not verify")
    void wrongKeyDoesNotVerify() {
        byte[] challenge = CryptoUtil.decodeBase64Url(CrossImplementationVectors.CHALLENGE);
        assertFalse(CryptoUtil.verifyChallenge(
                CrossImplementationVectors.DH_PUBLIC_KEY,   // the OTHER key
                challenge,
                CrossImplementationVectors.HOUSEHOLD_UID,
                CrossImplementationVectors.CHALLENGE_SIGNATURE));
    }

    @Test
    @DisplayName("garbage never throws out of verifyChallenge")
    void verifySurvivesGarbage() {
        assertFalse(CryptoUtil.verifyChallenge("!!!", new byte[32], "h_1", "also not base64"));
        assertFalse(CryptoUtil.verifyChallenge(
                CrossImplementationVectors.SIG_PUBLIC_KEY, new byte[32], "h_1", ""));
    }

    @Test
    @DisplayName("only valid on-curve P-256 points are accepted")
    void rejectsBadPublicKeys() {
        assertTrue(CryptoUtil.isValidPublicKey(CrossImplementationVectors.DH_PUBLIC_KEY));
        // Right length and prefix, but not a point on the curve — the
        // classic invalid-curve probe.
        byte[] offCurve = new byte[65];
        offCurve[0] = 0x04;
        java.util.Arrays.fill(offCurve, 1, 65, (byte) 0x11);
        assertFalse(CryptoUtil.isValidPublicKey(CryptoUtil.encodeBase64Url(offCurve)));
        // Wrong length.
        assertFalse(CryptoUtil.isValidPublicKey(CryptoUtil.encodeBase64Url(new byte[64])));
        // Compressed form, which this protocol does not use.
        byte[] compressed = new byte[33];
        compressed[0] = 0x02;
        assertFalse(CryptoUtil.isValidPublicKey(CryptoUtil.encodeBase64Url(compressed)));
        assertFalse(CryptoUtil.isValidPublicKey(""));
    }

    @Test
    @DisplayName("base64url is unpadded and round-trips")
    void base64UrlRoundTrip() {
        for (int length = 0; length < 40; length++) {
            byte[] raw = new byte[length];
            for (int i = 0; i < length; i++) {
                raw[i] = (byte) (i * 7);
            }
            String encoded = CryptoUtil.encodeBase64Url(raw);
            assertFalse(encoded.contains("="));
            if (length > 0) {
                assertArrayEquals(raw, CryptoUtil.decodeBase64Url(encoded));
            }
        }
    }

    @Test
    @DisplayName("tokens are 256 bits and never repeat")
    void tokensAreRandom() {
        String first = CryptoUtil.randomToken();
        assertEquals(32, CryptoUtil.decodeBase64Url(first).length);
        assertNotEquals(first, CryptoUtil.randomToken());
    }

    @Test
    @DisplayName("sha256Hex is the plain digest, so token lookups are comparable")
    void sha256HexIsStandard() {
        assertEquals(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                CryptoUtil.sha256Hex(""));
        assertEquals(64, CryptoUtil.sha256Hex(CryptoUtil.randomToken()).length());
    }

    @Test
    @DisplayName("constant-time comparison still compares correctly")
    void constantTimeEquals() {
        assertTrue(CryptoUtil.constantTimeEquals("abc", "abc"));
        assertFalse(CryptoUtil.constantTimeEquals("abc", "abd"));
        assertFalse(CryptoUtil.constantTimeEquals("abc", null));
        assertFalse(CryptoUtil.constantTimeEquals(null, "abc"));
    }

    @Test
    @DisplayName("uids are prefixed and unique")
    void uidsArePrefixed() {
        String uid = CryptoUtil.randomUid("h_");
        assertTrue(uid.startsWith("h_"));
        assertEquals(34, uid.length());
        assertNotEquals(uid, CryptoUtil.randomUid("h_"));
        assertDoesNotThrow(() -> HexFormat.of().parseHex(uid.substring(2)));
    }
}
