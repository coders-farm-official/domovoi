package com.domovoi.app.net

import kotlinx.serialization.Serializable
import java.io.IOException

/*
 * The day's grant on this phone (1.5.0; docs/SECURITY_PRIVACY.md, "Admin on
 * a phone: the day's grant"). After a password sign-in the phone may ask the
 * server for a grant (POST /api/auth/phone-grant): 256 random bits, bound to
 * this phone's device id, good for 24 hours from issue. The phone keeps it
 * ONLY encrypted under a Keystore key that the phone's strong biometrics
 * unlock, one use at a time ([GrantLocker]), and swaps it for a fresh admin
 * session when it needs one (POST /api/auth/phone-grant/exchange). Never the
 * password, never a Bearer on disk; the grant itself is never logged.
 *
 * A stored grant is bound to the server it came from AND the identity that
 * server proved then: it is offered only to that server, after it proves
 * that same key again. A different key (a re-pin, another machine) drops it.
 */

/** The grant as this phone keeps it: who issued it and the sealed bytes. */
@Serializable
data class StoredGrant(
    /** The server's base address, as [ApiClient.baseUrl] gives it (no trailing slash). */
    val server: String,
    /** The identity the server proved when it issued the grant. */
    val fingerprint: String,
    val deviceId: String,
    /** When the server said it ends (its clock decides; this is a hint). */
    val expiresAtMs: Long,
    /** [GrantLocker.Sealed] fields, base64. */
    val iv: String,
    val ciphertext: String,
)

/** Where the one stored grant lives (Prefs in the app; it is ciphertext). */
interface GrantShelf {
    fun readGrant(): StoredGrant?
    fun writeGrant(grant: StoredGrant?)
}

/**
 * The biometric-gated key. Both sealing and opening ask for the phone's
 * strong biometrics (the key needs a fresh authentication for every use).
 * Enrolling new biometrics invalidates the key: [open] then throws
 * [KeyGone] and the grant is dropped.
 */
interface GrantLocker {
    /** Strong biometrics are enrolled and the key can be made (Android 11+). */
    val available: Boolean

    class Sealed(val iv: ByteArray, val ciphertext: ByteArray)

    /** The person cancelled the prompt, or it failed: nothing changed. */
    class Cancelled(message: String) : IOException(message)

    /** The key is gone (new biometrics enrolled, or the lock screen
     *  removed): the grant can never be opened again. */
    class KeyGone : IOException("fingerprints or face on this phone changed; sign in with the password again")

    suspend fun seal(plain: ByteArray): Sealed

    suspend fun open(sealed: Sealed): ByteArray

    /** Deletes the key (sign-out, room mode): whatever was sealed is gone. */
    fun destroy()
}
