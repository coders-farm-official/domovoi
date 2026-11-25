package com.domovoi.app.data

import android.content.Context
import android.hardware.biometrics.BiometricManager
import android.hardware.biometrics.BiometricPrompt
import android.os.Build
import android.os.CancellationSignal
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import androidx.annotation.RequiresApi
import com.domovoi.app.net.GrantLocker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Today's admin grant, sealed under an AES-256-GCM Android Keystore key that
 * only the phone's strong biometrics unlock, for one operation at a time
 * (net/AdminGrant.kt). Android 11 and later: the key asks for a class 3
 * (strong) biometric on every use and is invalidated when new biometrics are
 * enrolled. On older phones [available] is false and the app asks for the
 * password each time instead.
 *
 * The framework's own BiometricPrompt (no extra library). [promptHost] is the
 * screen the prompt shows over (the Activity the person is using); the
 * application context is the fallback.
 */
class BiometricGrantLocker(private val appContext: Context) : GrantLocker {
    @Volatile var promptHost: Context? = null

    override val available: Boolean
        get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && strongBiometrics()

    @RequiresApi(Build.VERSION_CODES.R)
    private fun strongBiometricsR(): Boolean {
        val bm = appContext.getSystemService(BiometricManager::class.java) ?: return false
        return bm.canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) == BiometricManager.BIOMETRIC_SUCCESS
    }

    private fun strongBiometrics(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && strongBiometricsR()

    override suspend fun seal(plain: ByteArray): GrantLocker.Sealed {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R || !available) throw GrantLocker.KeyGone()
        return sealR(plain)
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private suspend fun sealR(plain: ByteArray): GrantLocker.Sealed {
        val cipher = withContext(Dispatchers.IO) {
            destroy()
            Cipher.getInstance(TRANSFORM).apply { init(Cipher.ENCRYPT_MODE, newKey()) }
        }
        val unlocked = prompt(cipher, "Keep admin on this phone today", "Domovoi keeps admin until the day's grant ends")
        return GrantLocker.Sealed(unlocked.iv, unlocked.doFinal(plain))
    }

    override suspend fun open(sealed: GrantLocker.Sealed): ByteArray {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) throw GrantLocker.KeyGone()
        return openR(sealed)
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private suspend fun openR(sealed: GrantLocker.Sealed): ByteArray {
        val cipher = withContext(Dispatchers.IO) {
            val key = existingKey() ?: throw GrantLocker.KeyGone()
            try {
                Cipher.getInstance(TRANSFORM).apply { init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, sealed.iv)) }
            } catch (e: KeyPermanentlyInvalidatedException) {
                destroy()
                throw GrantLocker.KeyGone()
            }
        }
        val unlocked = prompt(cipher, "Admin on this phone", "Use your fingerprint or face to go on as admin")
        return try {
            unlocked.doFinal(sealed.ciphertext)
        } catch (e: Exception) {
            throw GrantLocker.KeyGone()
        }
    }

    override fun destroy() {
        runCatching { keyStore().deleteEntry(ALIAS) }
    }

    @RequiresApi(Build.VERSION_CODES.R)
    private fun newKey(): SecretKey {
        val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        gen.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setUserAuthenticationRequired(true)
                // A strong biometric for every single use; no timeout window.
                .setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)
                .setInvalidatedByBiometricEnrollment(true)
                .build(),
        )
        return gen.generateKey()
    }

    private fun existingKey(): SecretKey? = runCatching { keyStore().getKey(ALIAS, null) as? SecretKey }.getOrNull()

    private fun keyStore(): KeyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }

    @RequiresApi(Build.VERSION_CODES.R)
    private suspend fun prompt(cipher: Cipher, title: String, subtitle: String): Cipher = withContext(Dispatchers.Main) {
        val ctx = promptHost ?: appContext
        suspendCancellableCoroutine { cont ->
            val signal = CancellationSignal()
            cont.invokeOnCancellation { signal.cancel() }
            val executor = ctx.mainExecutor
            val prompt = BiometricPrompt.Builder(ctx)
                .setTitle(title)
                .setSubtitle(subtitle)
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .setNegativeButton("Cancel", executor) { _, _ ->
                    if (cont.isActive) cont.resumeWithException(GrantLocker.Cancelled("cancelled"))
                }
                .build()
            prompt.authenticate(
                BiometricPrompt.CryptoObject(cipher),
                signal,
                executor,
                object : BiometricPrompt.AuthenticationCallback() {
                    override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                        val c = result.cryptoObject?.cipher
                        if (!cont.isActive) return
                        if (c != null) cont.resume(c) else cont.resumeWithException(GrantLocker.Cancelled("no key"))
                    }

                    override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                        if (cont.isActive) cont.resumeWithException(GrantLocker.Cancelled(errString.toString()))
                    }
                    // onAuthenticationFailed: one wrong finger; the prompt stays up.
                },
            )
        }
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val ALIAS = "domovoi_admin_day_grant"
        private const val TRANSFORM = "AES/GCM/NoPadding"
    }
}
