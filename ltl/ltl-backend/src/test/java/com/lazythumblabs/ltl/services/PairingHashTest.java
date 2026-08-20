package com.lazythumblabs.ltl.services;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static com.lazythumblabs.ltl.util.CrossImplementationVectors.PAIRING_CODE;
import static com.lazythumblabs.ltl.util.CrossImplementationVectors.PAIRING_CODE_HASH;
import static org.junit.jupiter.api.Assertions.*;

/**
 * Pairing hinges on two programs, in two languages, hashing the same
 * eight words to the same digest.
 *
 * <p>If these ever disagree, every pairing in production fails with
 * "that code doesn't match a server waiting to be paired" — a message
 * that would send everybody looking in exactly the wrong place. Hence
 * the vector.
 */
class PairingHashTest {

    @Test
    @DisplayName("hashes a code to the same digest the Python plugin does")
    void matchesPythonHash() {
        assertEquals(PAIRING_CODE_HASH, EnrollmentService.hashCode(PAIRING_CODE));
    }

    @Test
    @DisplayName("normalizes the way people actually retype eight words")
    void normalizesTypingVariants() {
        assertEquals(PAIRING_CODE_HASH,
                EnrollmentService.hashCode("MAPLE-HERON-BRICK-OAK-FERN-DAWN-OWL-RIVER"));
        assertEquals(PAIRING_CODE_HASH,
                EnrollmentService.hashCode("maple heron brick oak fern dawn owl river"));
        assertEquals(PAIRING_CODE_HASH,
                EnrollmentService.hashCode("  Maple  Heron,brick_oak.fern dawn owl river \n"));
    }

    @Test
    @DisplayName("a different code hashes differently")
    void differentCodesDiffer() {
        assertNotEquals(PAIRING_CODE_HASH,
                EnrollmentService.hashCode("maple-heron-brick-oak-fern-dawn-owl-raven"));
    }

    @Test
    @DisplayName("the hash is domain-separated from a bare SHA-256 of the words")
    void isDomainSeparated() throws Exception {
        String bare = java.util.HexFormat.of().formatHex(
                java.security.MessageDigest.getInstance("SHA-256")
                        .digest(PAIRING_CODE.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        assertNotEquals(bare, PAIRING_CODE_HASH);
    }

    @Test
    @DisplayName("a code that isn't eight words is refused with a usable message")
    void rejectsWrongWordCount() {
        EnrollmentService.EnrollmentException error = assertThrows(
                EnrollmentService.EnrollmentException.class,
                () -> EnrollmentService.hashCode("maple-heron"));
        assertEquals("INVALID_CODE", error.getCode());
        assertThrows(EnrollmentService.EnrollmentException.class,
                () -> EnrollmentService.hashCode(""));
        assertThrows(EnrollmentService.EnrollmentException.class,
                () -> EnrollmentService.hashCode(null));
    }
}
