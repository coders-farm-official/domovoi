package com.domovoi.app.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The phone's Ed25519 verifier is pinned to RFC 8032 (the same vectors
 * domovoi/tests/test_server_identity.py pins the core's signer to), and
 * the identity check accepts exactly what the core's `health_answer`
 * produces — and nothing that is not that (security round 3, A6-03).
 */
class ServerIdentityTest {

    private fun hex(s: String): ByteArray = s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    // RFC 8032 §7.1: (public key, message, signature).
    private val vectors = listOf(
        Triple(
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
            "",
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f" +
                "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
        ),
        Triple(
            "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
            "72",
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da08" +
                "5ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
        ),
        Triple(
            "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
            "af82",
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18" +
                "ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
        ),
    )

    @Test fun theVerifierMatchesRfc8032() {
        for ((pub, msg, sig) in vectors) {
            assertTrue(Ed25519.verify(hex(pub), hex(msg), hex(sig)))
        }
    }

    @Test fun theTestOnlySignerMatchesRfc8032Too() {
        // So a fake server in the gate tests signs exactly as the core does.
        val seeds = listOf(
            "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
            "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
            "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        )
        for ((i, seed) in seeds.withIndex()) {
            val (pub, msg, sig) = vectors[i]
            assertEquals(pub, Ed25519.publicKey(hex(seed)).joinToString("") { "%02x".format(it) })
            assertEquals(sig, Ed25519.sign(hex(seed), hex(msg)).joinToString("") { "%02x".format(it) })
        }
    }

    @Test fun aSignatureIsRefusedForAnotherMessageAnotherKeyOrWhenBent() {
        val (pub, _, sig) = vectors[1]
        assertFalse(Ed25519.verify(hex(pub), hex("73"), hex(sig)))
        assertFalse(Ed25519.verify(hex(vectors[0].first), hex("72"), hex(sig)))
        val bent = hex(sig).also { it[10] = (it[10].toInt() xor 1).toByte() }
        assertFalse(Ed25519.verify(hex(pub), hex("72"), bent))
    }

    @Test fun malformedInputIsAnAnswerNotAnException() {
        assertFalse(Ed25519.verify(ByteArray(0), "m".toByteArray(), ByteArray(0)))
        assertFalse(Ed25519.verify(ByteArray(32), "m".toByteArray(), ByteArray(64) { 0xff.toByte() }))
        assertFalse(Ed25519.verify(ByteArray(31), "m".toByteArray(), ByteArray(64)))
        assertFalse(Ed25519.verify(hex(vectors[0].first), "m".toByteArray(), ByteArray(63)))
    }

    // ---- what the core answers ---------------------------------------------

    /** `/v1/health?challenge=…` as domovoi.server_identity.health_answer
     *  produced it for the RFC 8032 vector-1 seed (generated with the core's
     *  own code; see gen_fixture in the remediation notes). */
    private val challenge = "0123456789abcdef0123456789abcdef"
    private val fingerprint = "SHA256:If4x36FUomFia/hUBG/SJxt77UtqvkWqWId+9H+XIbk"
    private val publicKey = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
    private val coreAnswer = """{"status":"ok","bot_name":"domovoi","stt":"ok","identity":{"algorithm":"ed25519",""" +
        """"fingerprint":"$fingerprint","public_key":"$publicKey","challenge":"$challenge",""" +
        """"signature":"KrwTHDAWdBmX8oh0dVDsMqBdNPKS6ZXw5ttWTGNEEkXKH8cB+ht9O3UbpXCYBR6tJ0x5UH+78V2jP29QP4bhDA=="}}"""

    @Test fun theCoresOwnAnswerVerifiesAndPinsItsKey() {
        val proof = ServerIdentity.check(coreAnswer, challenge, pinned = null)
        assertEquals(ServerIdentity.Proof.Verified(ServerIdentity.Pin(publicKey, fingerprint)), proof)
        // ...and against the pin it just made.
        assertEquals(proof, ServerIdentity.check(coreAnswer, challenge, (proof as ServerIdentity.Proof.Verified).pin))
    }

    @Test fun theFingerprintIsTheCoresShape() {
        val key = java.util.Base64.getDecoder().decode(publicKey)
        assertEquals(fingerprint, ServerIdentity.fingerprintOf(key))
        assertEquals(hex(vectors[0].first).toList(), key.toList())
    }

    @Test fun aReplayedAnswerSignsSomebodyElsesChallenge() {
        val proof = ServerIdentity.check(coreAnswer, "ffffffffffffffffffffffffffffffff", pinned = null)
        assertEquals(ServerIdentity.Proof.Invalid("our challenge was not echoed"), proof)
        // An attacker who rewrites the echoed challenge still has the
        // wrong signature.
        val rewritten = coreAnswer.replace(challenge, "ffffffffffffffffffffffffffffffff")
        assertEquals(
            ServerIdentity.Proof.Invalid("the signature does not verify"),
            ServerIdentity.check(rewritten, "ffffffffffffffffffffffffffffffff", pinned = null),
        )
    }

    @Test fun aDifferentKeyThanThePinnedOneIsAMismatchEvenWhenItProvesItself() {
        val pinned = ServerIdentity.Pin("c29tZWJvZHkgZWxzZXMga2V5IGhlcmUgLS0tLS0tLS0=", "SHA256:someoneelse")
        assertEquals(
            ServerIdentity.Proof.Mismatch("SHA256:someoneelse", fingerprint),
            ServerIdentity.check(coreAnswer, challenge, pinned),
        )
    }

    @Test fun aWebWhoseCoreIsNotAnsweringIsItsOwnCaseNotAServerWithoutIdentity() {
        // web/backend/main.py leaves identity out whenever /v1/health did not
        // answer 200 within 2 s and says so in domovoi_reachable: a restart,
        // an update, a busy box. Not "this server has no identity".
        assertEquals(
            ServerIdentity.Proof.CoreNotAnswering,
            ServerIdentity.check(
                """{"status":"degraded","db_reachable":true,"domovoi_reachable":false,"stt":null,"identity":null}""",
                challenge, null,
            ),
        )
        // A core that answered without an identity block, or a web backend
        // too old to say either, is a server from before identity.
        assertEquals(
            ServerIdentity.Proof.NoIdentity,
            ServerIdentity.check("""{"status":"ok","domovoi_reachable":true}""", challenge, null),
        )
        assertEquals(ServerIdentity.Proof.NoIdentity, ServerIdentity.check("""{"status":"ok"}""", challenge, null))
    }

    @Test fun anAdvertisedIdentityIsTakenOnlyWhenItHoldsTogether() {
        assertEquals(ServerIdentity.Pin(publicKey, fingerprint), ServerIdentity.advertised("ed25519", publicKey, fingerprint))
        assertEquals(null, ServerIdentity.advertised("rsa", publicKey, fingerprint))
        assertEquals(null, ServerIdentity.advertised("ed25519", publicKey, "SHA256:nope"))
        assertEquals(null, ServerIdentity.advertised("ed25519", "AAAA", fingerprint))
        assertEquals(null, ServerIdentity.advertised("ed25519", null, fingerprint))
        assertEquals(null, ServerIdentity.advertised("ed25519", publicKey, null))
    }

    @Test fun noIdentityBlockIsNoIdentityAndEverythingElseIsInvalid() {
        assertEquals(ServerIdentity.Proof.NoIdentity, ServerIdentity.check("""{"status":"ok"}""", challenge, null))
        assertTrue(ServerIdentity.check("not json", challenge, null) is ServerIdentity.Proof.Invalid)
        assertTrue(ServerIdentity.check(null, challenge, null) is ServerIdentity.Proof.Invalid)
        assertEquals(
            ServerIdentity.Proof.Invalid("unknown identity algorithm"),
            ServerIdentity.check(coreAnswer.replace("ed25519", "rsa"), challenge, null),
        )
        assertEquals(
            ServerIdentity.Proof.Invalid("the fingerprint is not the key's"),
            ServerIdentity.check(coreAnswer.replace(fingerprint, "SHA256:nope"), challenge, null),
        )
        assertEquals(
            ServerIdentity.Proof.Invalid("malformed public key"),
            ServerIdentity.check(coreAnswer.replace(publicKey, "AAAA"), challenge, null),
        )
    }

    @Test fun challengesAreFreshAndTheShapeTheCoreAccepts() {
        val a = ServerIdentity.newChallenge()
        val b = ServerIdentity.newChallenge()
        assertEquals(32, a.length)
        assertTrue(a.all { it in '0'..'9' || it in 'a'..'f' })
        assertFalse(a == b)
    }

    @Test fun thePinBookSurvivesARoundTripAndACorruptBlobIsNotFatal() {
        val pins = mapOf("http://10.0.0.42:6369" to ServerIdentity.Pin(publicKey, fingerprint))
        assertEquals(pins, ServerIdentity.decodePins(ServerIdentity.encodePins(pins)))
        assertEquals(emptyMap<String, ServerIdentity.Pin>(), ServerIdentity.decodePins("{"))
        assertEquals(emptyMap<String, ServerIdentity.Pin>(), ServerIdentity.decodePins(null))
    }
}
