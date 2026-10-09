package com.domovoi.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * The household tokens rest sealed (AES-256-GCM) and nowhere in the clear;
 * a blob the key did not seal is "nothing paired", never a crash; and the
 * plain DataStore value an older install (or the test harness) wrote is
 * swept in (security round 3, A6-04). The Keystore itself needs a device,
 * so the sealer here is the same cipher over a software key.
 */
class TokenVaultTest {

    /** The production sealer's format with a JVM key: 12-byte IV ‖ ct ‖ tag,
     *  the same AAD. */
    private class SoftwareSealer(private val key: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()) :
        TokenVault.Sealer {
        override fun seal(plain: ByteArray): ByteArray {
            val cipher = Cipher.getInstance(KeystoreSealer.TRANSFORMATION)
            cipher.init(Cipher.ENCRYPT_MODE, key)
            cipher.updateAAD(KeystoreSealer.AAD)
            return cipher.iv + cipher.doFinal(plain)
        }

        override fun open(sealed: ByteArray): ByteArray {
            val cipher = Cipher.getInstance(KeystoreSealer.TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(KeystoreSealer.TAG_BITS, sealed, 0, KeystoreSealer.IV_BYTES))
            cipher.updateAAD(KeystoreSealer.AAD)
            return cipher.doFinal(sealed, KeystoreSealer.IV_BYTES, sealed.size - KeystoreSealer.IV_BYTES)
        }
    }

    private class MemoryStore(var value: String? = null, var reachesDisk: Boolean = true) : TokenVault.Store {
        override fun load() = value
        override fun save(sealed: String?): Boolean {
            if (!reachesDisk) return false
            value = sealed
            return true
        }
    }

    private val tokens = mapOf("http://10.0.0.42:6369" to "acorn-maple-river-thistle", "http://10.0.0.43:6369" to "second-house-token")

    @Test fun tokensSurviveARoundTripAndNeverAppearInTheClear() {
        val store = MemoryStore()
        val vault = TokenVault(store, SoftwareSealer())
        vault.write(tokens)
        val raw = store.value!!
        assertTrue(raw.startsWith("v1:"))
        for (t in tokens.values) assertFalse("token in the clear", raw.contains(t))
        assertFalse("server address in the clear", raw.contains("10.0.0.42"))
        assertEquals(tokens, vault.read())
    }

    @Test fun anEmptyBookRemovesTheRecord() {
        val store = MemoryStore("v1:whatever")
        TokenVault(store, SoftwareSealer()).write(emptyMap())
        assertNull(store.value)
        assertEquals(emptyMap<String, String>(), TokenVault(store, SoftwareSealer()).read())
    }

    @Test fun aBlobAnotherKeySealedOrACorruptOneIsNothingPairedNotACrash() {
        val store = MemoryStore()
        TokenVault(store, SoftwareSealer()).write(tokens)
        val log = mutableListOf<String>()
        // A different key: the phone lost its Keystore key (a factory reset
        // restored the files but not the key).
        assertEquals(emptyMap<String, String>(), TokenVault(store, SoftwareSealer(), log::add).read())
        assertTrue(log.single().contains("could not open"))
        // Bent bytes under the same key.
        val sealer = SoftwareSealer()
        TokenVault(store, sealer).write(tokens)
        store.value = store.value!!.dropLast(4) + "AAAA"
        assertEquals(emptyMap<String, String>(), TokenVault(store, sealer).read())
        // Not even the right shape.
        store.value = "device_tokens={...}"
        assertEquals(emptyMap<String, String>(), TokenVault(store, sealer, log::add).read())
        assertTrue(log.last().contains("unknown format"))
    }

    @Test fun eachSealIsFresh() {
        val store = MemoryStore()
        val vault = TokenVault(store, SoftwareSealer())
        vault.write(tokens)
        val first = store.value
        vault.write(tokens)
        assertFalse("a random IV per seal: the same tokens never seal the same way", first == store.value)
    }

    @Test fun aSealerThatFailsIsLoggedNotThrownAndTheRecordStands() {
        // A Keystore that will not hand out its key (seen in the wild on
        // phones with a broken keymaster): the pairing path must not crash
        // and what was sealed before must stay readable.
        val store = MemoryStore()
        val good = SoftwareSealer()
        TokenVault(store, good).write(tokens)
        val before = store.value
        val log = mutableListOf<String>()
        val broken = object : TokenVault.Sealer {
            override fun seal(plain: ByteArray): ByteArray = throw IllegalStateException("keystore unavailable")
            override fun open(sealed: ByteArray): ByteArray = good.open(sealed)
        }
        val vault = TokenVault(store, broken, log::add)
        assertFalse(vault.write(tokens + ("http://10.0.0.44:6369" to "third-house-token")))
        assertEquals("the record on disk is untouched", before, store.value)
        assertTrue(log.single().contains("could not seal"))
        assertEquals("and still opens", tokens, vault.read())
        assertTrue("an empty book needs no sealer", TokenVault(store, broken).write(emptyMap()))
        assertNull(store.value)
    }

    @Test fun aRecordThatDidNotReachDiskIsSaidSoAndNotCountedAsMoved() {
        // Prefs removes the plain DataStore copy only on a true from here,
        // so the answer has to be the store's commit, not a wish.
        val store = MemoryStore()
        val sealer = SoftwareSealer()
        assertTrue(TokenVault(store, sealer).write(tokens))
        val before = store.value
        store.reachesDisk = false
        val log = mutableListOf<String>()
        assertFalse(TokenVault(store, sealer, log::add).write(tokens + ("http://10.0.0.44:6369" to "third-house-token")))
        assertTrue(log.single().contains("did not reach disk"))
        assertEquals("the record on disk is untouched", before, store.value)
        assertFalse("an empty book that did not commit is not an empty book", TokenVault(store, sealer).write(emptyMap()))
    }

    @Test fun theSweepLaysThePlainValueOverTheVault() {
        val vault = mapOf("http://a:6369" to "old-a", "http://b:6369" to "vault-b")
        val plain = mapOf("http://a:6369" to "new-a", "http://c:6369" to "plain-c", "http://d:6369" to "  ")
        assertEquals(
            mapOf("http://a:6369" to "new-a", "http://b:6369" to "vault-b", "http://c:6369" to "plain-c"),
            TokenVault.merged(vault, plain),
        )
        assertEquals(vault, TokenVault.merged(vault, emptyMap()))
    }
}
