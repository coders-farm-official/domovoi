package com.domovoi.app.assistant.lookup

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Seals a secret so only this app, on this phone, can open it. */
interface SecretBox {
    fun seal(plain: String): String
    fun open(sealed: String): String?
}

/** The Android keystore: an AES-GCM key that never leaves the phone's
 *  secure hardware (where it has some), made on first use. */
class KeystoreBox(private val alias: String = "domovoi-own-search") : SecretBox {
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        gen.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return gen.generateKey()
    }

    override fun seal(plain: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        val out = c.iv + c.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    override fun open(sealed: String): String? = runCatching {
        val all = Base64.decode(sealed, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, all.copyOfRange(0, 12)))
        String(c.doFinal(all.copyOfRange(12, all.size)), Charsets.UTF_8)
    }.getOrNull()
}

/** For tests: "sealed" in memory, readable only through [open]. */
class MemoryBox : SecretBox {
    private val kept = HashMap<String, String>()
    override fun seal(plain: String): String = "sealed-${kept.size}".also { kept[it] = plain }
    override fun open(sealed: String): String? = kept[sealed]
}

/** Where the phone keeps "Use your own search" (Settings → Assistant):
 *  the choice, the SearXNG address and the Brave key, each sealed by
 *  [box] in the app's private prefs (outside every backup, like the rest).
 *  Nothing here is sent anywhere but to the search it names. */
interface OwnSearchStore {
    var own: OwnSearch?
}

class MemoryOwnSearchStore(override var own: OwnSearch? = null) : OwnSearchStore

class PhoneOwnSearchStore(context: Context, private val box: SecretBox = KeystoreBox()) : OwnSearchStore {
    private val prefs = context.applicationContext.getSharedPreferences("assistant-search", Context.MODE_PRIVATE)

    override var own: OwnSearch?
        get() {
            val kind = prefs.getString(K_KIND, null) ?: return null
            val sealed = prefs.getString(K_SECRET, null) ?: return null
            val secret = box.open(sealed) ?: return null
            return when (kind) {
                OwnSearch.BRAVE -> OwnSearch(kind, key = secret)
                OwnSearch.SEARXNG -> OwnSearch(kind, url = secret)
                else -> null
            }
        }
        set(value) {
            val e = prefs.edit()
            if (value == null) e.remove(K_KIND).remove(K_SECRET)
            else e.putString(K_KIND, value.kind).putString(K_SECRET, box.seal(if (value.kind == OwnSearch.BRAVE) value.key else value.url))
            e.apply()
        }

    private companion object {
        const val K_KIND = "kind"
        const val K_SECRET = "secret"
    }
}
