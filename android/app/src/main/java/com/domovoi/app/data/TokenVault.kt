package com.domovoi.app.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Where the household device tokens rest: encrypted, under a key that
 * never leaves the Android Keystore (security round 3, A6-04).
 *
 * Until 2026-10-08 the tokens sat as JSON in the plain Preferences
 * DataStore (`device_tokens`), while the docs said
 * `EncryptedSharedPreferences`. They now live in their own file as one
 * AES-256-GCM sealed blob (`v1:` + base64 of 12-byte IV ‖ ciphertext ‖
 * tag), the key being a non-exportable Keystore key the phone generates
 * once. The file is still excluded from every backup and transfer.
 *
 * The migration is a SWEEP, not a one-shot: at every start, a `device_tokens`
 * value found in the DataStore is merged into the vault and the plain key
 * removed — only once the vault's record is on disk ([Store.save] commits
 * and says so), so a process killed between the two writes never leaves
 * the token in neither file. That is what moves an existing install's token across once —
 * and what lets a test harness that writes the plain key (functional
 * testing's at.py) keep working: the value it writes is swept into the
 * vault at the next start.
 *
 * What this does and does not protect. A copy of the app's files (a
 * backup, a lost phone's storage read offline) holds ciphertext without
 * the key. It does NOT stop code running as the app's own uid from asking
 * the Keystore to decrypt — and on a debuggable build (the CI APK) `adb
 * shell run-as` and an attached debugger ARE that code. Only a release
 * build closes that; android/README.md says so.
 *
 * Pure where it can be: the sealing is behind [Sealer] so the format, the
 * migration and the corrupt-blob rule run on the JVM with a software key.
 */
class TokenVault(
    private val store: Store,
    private val sealer: Sealer,
    private val log: (String) -> Unit = {},
) {
    /** The one record the vault keeps. */
    interface Store {
        fun load(): String?

        /** True once the record is ON DISK (SharedPreferences.commit, not
         *  apply): the plain DataStore copy is removed only on a true, so
         *  a process killed between the two writes leaves the token in one
         *  file or the other, never in neither (A6-04 review). */
        fun save(sealed: String?): Boolean
    }

    /** AES-GCM over the Keystore key, or a software key in tests. */
    interface Sealer {
        fun seal(plain: ByteArray): ByteArray

        /** Throws (javax.crypto.AEADBadTagException and friends) on a blob
         *  this key did not seal. */
        fun open(sealed: ByteArray): ByteArray
    }

    /** The tokens, by server. Empty — and said so — when there is nothing,
     *  or when what is there cannot be opened (a key the phone lost, a
     *  corrupt file): the phone then asks to pair again. */
    fun read(): Map<String, String> {
        val raw = store.load() ?: return emptyMap()
        if (!raw.startsWith(PREFIX)) {
            log("token vault: unknown format; starting empty")
            return emptyMap()
        }
        return try {
            val plain = sealer.open(Base64.getDecoder().decode(raw.removePrefix(PREFIX)))
            ServerCredentials.decodeTokens(String(plain, Charsets.UTF_8))
        } catch (e: Exception) {
            log("token vault: could not open the sealed tokens (${e.javaClass.simpleName}); starting empty")
            emptyMap()
        }
    }

    /**
     * Seal and store [tokens]. True once the record is on disk. False —
     * and a log line — when the sealer fails (a Keystore that will not
     * hand out its key) or the store could not commit; the record on disk
     * is then left as it was. Never throws: this runs from a coroutine on
     * the pairing path, where an exception would take the app down with
     * the token in its message.
     */
    fun write(tokens: Map<String, String>): Boolean {
        if (tokens.isEmpty()) return store.save(null)
        val plain = ServerCredentials.encodeTokens(tokens).toByteArray(Charsets.UTF_8)
        val sealed = try {
            sealer.seal(plain)
        } catch (e: Exception) {
            log("token vault: could not seal the tokens (${e.javaClass.simpleName}); nothing written")
            return false
        }
        val written = store.save(PREFIX + Base64.getEncoder().encodeToString(sealed))
        if (!written) log("token vault: the sealed tokens did not reach disk; the record stands as it was")
        return written
    }

    companion object {
        const val PREFIX = "v1:"

        /** What a sweep of the plain DataStore value yields: the vault's
         *  tokens with the plain ones laid over them (a value the harness
         *  just wrote is the newer word). Pure. */
        fun merged(vault: Map<String, String>, plain: Map<String, String>): Map<String, String> =
            vault + plain.filterValues { it.isNotBlank() }
    }
}

/** The vault's record as a SharedPreferences entry of its own, in its own
 *  file: `shared_prefs/domovoi-vault.xml`. (SharedPreferences, not the
 *  DataStore, so a harness rewriting the DataStore blob leaves it be.) */
class PrefsVaultStore(context: Context) : TokenVault.Store {
    private val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    override fun load(): String? = prefs.getString(KEY, null)

    /** commit(), not apply(): the answer says whether the record is on
     *  disk, which is what removing the plain copy is conditioned on. The
     *  write is small and runs off the main thread on the pairing path
     *  (and once, at start, inside the blocking preferences read). */
    override fun save(sealed: String?): Boolean =
        prefs.edit().apply { if (sealed == null) remove(KEY) else putString(KEY, sealed) }.commit()

    private companion object {
        const val FILE = "domovoi-vault"
        const val KEY = "device_tokens"
    }
}

/**
 * AES-256-GCM with a key generated into the Android Keystore the first
 * time: non-exportable, hardware-backed where the phone has that, no
 * user authentication required (the token must be usable with the screen
 * locked — the background timer sync runs then).
 */
class KeystoreSealer : TokenVault.Sealer {
    private val key: SecretKey by lazy { loadOrCreateKey() }

    override fun seal(plain: ByteArray): ByteArray {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        check(iv.size == IV_BYTES) { "unexpected GCM iv size ${iv.size}" }
        cipher.updateAAD(AAD)
        return iv + cipher.doFinal(plain)
    }

    override fun open(sealed: ByteArray): ByteArray {
        require(sealed.size > IV_BYTES) { "sealed blob too short" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, sealed, 0, IV_BYTES))
        cipher.updateAAD(AAD)
        return cipher.doFinal(sealed, IV_BYTES, sealed.size - IV_BYTES)
    }

    private fun loadOrCreateKey(): SecretKey {
        val ks = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    companion object {
        const val ALIAS = "domovoi-token-vault"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
        val AAD: ByteArray = "domovoi-device-tokens-v1".toByteArray(Charsets.UTF_8)
    }
}
