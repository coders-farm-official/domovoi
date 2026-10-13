package com.domovoi.app.roommode

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import com.domovoi.app.data.KeystoreSealer
import com.domovoi.app.data.TokenVault
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.security.SecureRandom
import java.util.Base64

/**
 * Everything a room phone keeps, apart from the household app's own store:
 *
 * - the enrolment (the core's address, the PINNED server fingerprint, the
 *   room), the approval code it shows, the wake word the core pushed;
 * - the room's pairing key: 32 random bytes minted here, sealed under its
 *   own Android Keystore key (`domovoi-room-key`, AES-GCM, the household
 *   vault's format) in its own file. Only its sha256 ever reaches the core.
 *   Never the household device token: a room phone holds no household
 *   credential (owner decision 2026-10-12), and on a phone that is also a
 *   household device that token stays sealed in the app's vault, unused,
 *   while room mode is on ([active]);
 * - whether room mode is ON ([active]: the normal app is locked until the
 *   person exits with the phone's unlock) and whether it was RUNNING when
 *   the phone went down (the after-reboot "tap to resume");
 * - the serial of the last signed list accepted per channel.
 *
 * Files: `shared_prefs/domovoi-room.xml` and `shared_prefs/domovoi-room-vault.xml`,
 * excluded from every backup and transfer like the rest of the app's data.
 */
class RoomStore(context: Context) {
    private val prefs: SharedPreferences = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)
    private val vault = TokenVault(
        VaultFile(context.applicationContext.getSharedPreferences(VAULT_FILE, Context.MODE_PRIVATE)),
        KeystoreSealer(alias = KEY_ALIAS, aad = KEY_AAD),
    ) { Log.w(TAG, it) }

    val enrolment: Enrolment?
        get() {
            val core = prefs.getString(K_CORE, null) ?: return null
            val fp = prefs.getString(K_FP, null) ?: return null
            val room = prefs.getString(K_ROOM, null) ?: return null
            return Enrolment(core, fp, room, prefs.getString(K_LABEL, null) ?: room)
        }

    /** Room mode is on: the normal app is locked (MainActivity sends the
     *  person here) and the household token is never put on a request. */
    val active: Boolean get() = prefs.getBoolean(K_ACTIVE, false)

    /** The room was listening (not stopped from the notification) — what a
     *  reboot resumes with one tap. */
    val running: Boolean get() = prefs.getBoolean(K_RUNNING, false)

    val approvalCode: String? get() = prefs.getString(K_CODE, null)

    val wakeWord: String get() = prefs.getString(K_WAKE, null) ?: DEFAULT_WAKE_WORD

    val wakeThreshold: Float get() = prefs.getFloat(K_THRESHOLD, DEFAULT_THRESHOLD)

    val muted: Boolean get() = prefs.getBoolean(K_MUTED, false)

    /**
     * Become the room [e]. A NEW pairing key and approval code go with a new
     * room or server; re-entering the same room keeps both (the core knows
     * this phone by its key's hash). Returns false when the key could not
     * be sealed (a Keystore that will not hand out its key).
     */
    fun enrol(e: Enrolment): Boolean {
        val same = enrolment?.let { it.coreUrl == e.coreUrl && it.roomId == e.roomId && it.fingerprint == e.fingerprint } == true
        if (!same || pairingToken() == null) {
            if (!vault.write(mapOf(TOKEN_KEY to newToken()))) return false
            prefs.edit().putString(K_CODE, newApprovalCode()).remove(K_SERIALS).remove(K_WAKE).remove(K_THRESHOLD).commit()
        }
        prefs.edit()
            .putString(K_CORE, e.coreUrl)
            .putString(K_FP, e.fingerprint)
            .putString(K_ROOM, e.roomId)
            .putString(K_LABEL, e.roomLabel)
            .commit()
        return true
    }

    /** The room's pairing key, or null when there is none (or it can no
     *  longer be opened: the phone then enrols again). */
    fun pairingToken(): String? = vault.read()[TOKEN_KEY]?.takeIf { it.isNotBlank() }

    fun setActive(on: Boolean) {
        prefs.edit().putBoolean(K_ACTIVE, on).apply { if (!on) putBoolean(K_RUNNING, false) }.commit()
    }

    fun setRunning(on: Boolean) {
        prefs.edit().putBoolean(K_RUNNING, on).apply()
    }

    fun setMuted(on: Boolean) {
        prefs.edit().putBoolean(K_MUTED, on).apply()
    }

    /** The code the core has on file for this request wins over ours. */
    fun setApprovalCode(code: String) {
        if (code.length in 4..8 && code.all { it in '0'..'9' }) prefs.edit().putString(K_CODE, code).apply()
    }

    fun renewApprovalCode() {
        prefs.edit().putString(K_CODE, newApprovalCode()).apply()
    }

    fun setWakeWord(slug: String, threshold: Double?) {
        prefs.edit().putString(K_WAKE, slug).apply {
            if (threshold != null && threshold > 0.0 && threshold < 1.0) putFloat(K_THRESHOLD, threshold.toFloat()) else remove(K_THRESHOLD)
        }.apply()
    }

    fun seen(record: String): RoomIdentity.Seen? {
        val raw = serials()[record] ?: return null
        val serial = raw.substringBefore(':').toLongOrNull() ?: return null
        return RoomIdentity.Seen(serial, raw.substringAfter(':', "").ifEmpty { null })
    }

    fun remember(record: String, accepted: RoomIdentity.Accepted) {
        val next = serials() + (record to "${accepted.serial}:${accepted.digest}")
        prefs.edit().putString(K_SERIALS, Json.encodeToString(SERIALS, next)).apply()
    }

    private fun serials(): Map<String, String> =
        runCatching { Json.decodeFromString(SERIALS, prefs.getString(K_SERIALS, null) ?: "{}") }.getOrDefault(emptyMap())

    /** Forget the room entirely: key, code, enrolment (exit + "forget"). */
    fun forget() {
        vault.write(emptyMap())
        prefs.edit().clear().commit()
    }

    private class VaultFile(private val prefs: SharedPreferences) : TokenVault.Store {
        override fun load(): String? = prefs.getString("room_key", null)

        override fun save(sealed: String?): Boolean =
            prefs.edit().apply { if (sealed == null) remove("room_key") else putString("room_key", sealed) }.commit()
    }

    companion object {
        private const val TAG = "RoomStore"
        const val FILE = "domovoi-room"
        const val VAULT_FILE = "domovoi-room-vault"
        const val KEY_ALIAS = "domovoi-room-key"
        val KEY_AAD: ByteArray = "domovoi-room-key-v1".toByteArray(Charsets.UTF_8)
        const val DEFAULT_WAKE_WORD = "hey_jarvis"
        const val DEFAULT_THRESHOLD = 0.5f
        private const val TOKEN_KEY = "room"
        private const val K_CORE = "core_url"
        private const val K_FP = "fingerprint"
        private const val K_ROOM = "room_id"
        private const val K_LABEL = "room_label"
        private const val K_ACTIVE = "active"
        private const val K_RUNNING = "running"
        private const val K_CODE = "approval_code"
        private const val K_WAKE = "wake_word"
        private const val K_THRESHOLD = "wake_threshold"
        private const val K_MUTED = "muted"
        private const val K_SERIALS = "manifest_serials"
        private val SERIALS = MapSerializer(String.serializer(), String.serializer())

        /** 32 random bytes, URL-safe base64 (as `secrets.token_urlsafe(32)`). */
        fun newToken(random: SecureRandom = SecureRandom()): String =
            Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32).also { random.nextBytes(it) })

        /** Six digits, what the dashboard's approval card takes. */
        fun newApprovalCode(random: SecureRandom = SecureRandom()): String =
            (random.nextInt(900_000) + 100_000).toString()

        /** Whether room mode is on, without building a store (MainActivity). */
        fun isActive(context: Context): Boolean =
            context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE).getBoolean(K_ACTIVE, false)
    }
}
