package com.domovoi.app.roommode

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import com.domovoi.app.data.KeystoreSealer
import com.domovoi.app.data.TokenVault
import com.domovoi.app.net.HouseholdLock
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.security.SecureRandom
import java.util.Base64

/**
 * Everything a room phone keeps, apart from the household app's own store:
 *
 * - the enrolment (the core's address, the PINNED server fingerprint, the
 *   phone's own id, and the room it asked to join until it is approved),
 *   the approval code it shows, the wake word the core pushed, and the
 *   name of the room it is in as the server last said it;
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
class RoomStore(context: Context) : RoomModels.ModelBook {
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
            return Enrolment(core, fp, room, prefs.getString(K_LABEL, null) ?: room, requestedRoom)
        }

    /**
     * The room this phone asked to join (the room code's `in=`, 1.5.0),
     * sent in every hello as `requested_room` until the server accepts the
     * phone (its first `ready`, [clearRequestedRoom]); null: none asked for,
     * or already approved. It only pre-selects the approval card's room.
     */
    val requestedRoom: String?
        get() = prefs.getString(K_REQUESTED, null)?.takeIf { RoomEnrolment.isRoomId(it) }

    /** Approved: the server decides the room from here on. */
    fun clearRequestedRoom() {
        if (prefs.contains(K_REQUESTED)) prefs.edit().remove(K_REQUESTED).apply()
    }

    /**
     * The name of the room this phone is in, as the server last said it
     * (`ready.room` / the `room` frame), or before that the room the room
     * code said it joins; null: only the room code's own name is known
     * ([Enrolment.roomLabel]). Kept so the screen and the notification say
     * the room's name before the next connect.
     */
    val roomName: String? get() = prefs.getString(K_ROOM_NAME, null)?.takeIf { it.isNotBlank() }

    fun setRoomName(name: String) {
        if (name.isNotBlank() && name != roomName) prefs.edit().putString(K_ROOM_NAME, name).apply()
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

    /** Every word the room was told to listen for, main word ([wakeWord])
     *  first (1.4.0, [WakeWords]): the kept list when it starts with the
     *  main word, else the main word alone. */
    val wakeWords: List<WakeWords.Word>
        get() = keptWords(wakeWord, prefs.getString(K_WAKE_WORDS, null))

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
            prefs.edit().putString(K_CODE, newApprovalCode()).remove(K_SERIALS).remove(K_WAKE).remove(K_WAKE_WORDS).remove(K_THRESHOLD)
                .remove(K_APPROVED).remove(K_ROOM_NAME).commit()
        }
        prefs.edit()
            .putString(K_CORE, e.coreUrl)
            .putString(K_FP, e.fingerprint)
            .putString(K_ROOM, e.roomId)
            .putString(K_LABEL, e.roomLabel)
            .apply {
                // The room the code says this phone joins (`in=`): asked for
                // in each hello until the server accepts the phone.
                val asked = e.inRoom?.takeIf { RoomEnrolment.isRoomId(it) }
                if (asked != null) putString(K_REQUESTED, asked) else remove(K_REQUESTED)
                e.inRoomName?.takeIf { asked != null && it.isNotBlank() }?.let { putString(K_ROOM_NAME, it) }
            }
            .commit()
        return true
    }

    /** The room's pairing key, or null when there is none (or it can no
     *  longer be opened: the phone then enrols again). */
    fun pairingToken(): String? = vault.read()[TOKEN_KEY]?.takeIf { it.isNotBlank() }

    fun setActive(on: Boolean) {
        // The household token goes on no request from this moment
        // (net/HouseholdLock.kt), before anything else happens.
        HouseholdLock.roomModeActive = on
        prefs.edit().putBoolean(K_ACTIVE, on).apply { if (!on) putBoolean(K_RUNNING, false) }.commit()
    }

    /** The core accepted this room at least once (a `ready`): a later
     *  "waiting for approval" means it was removed on the dashboard. */
    val approvedOnce: Boolean get() = prefs.getBoolean(K_APPROVED, false)

    fun setApprovedOnce() {
        if (!approvedOnce) prefs.edit().putBoolean(K_APPROVED, true).apply()
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

    /**
     * A pushed wake word (`set_wake_word`). Its [threshold], when it is a
     * 0-1 value ([thresholdValue]), becomes the room's one threshold
     * ([wakeThreshold], reported as `wake.threshold`): the Pi writes it into
     * its `[wake] threshold` setting the same way. Pushed without one (or
     * with an unusable one) the threshold stays as it is, as on a Pi.
     *
     * [words] (1.4.0): the words the room now runs, [slug] first
     * ([WakeWords.plan], already cut to this phone's limit); one word keeps
     * no list (an older core's push drops any extras kept before).
     */
    fun setWakeWord(slug: String, threshold: Double?, words: List<WakeWords.Word> = listOf(WakeWords.Word(slug))) {
        prefs.edit().putString(K_WAKE, slug).apply {
            thresholdValue(threshold)?.let { putFloat(K_THRESHOLD, it) }
            if (words.size > 1 && words[0].slug == slug) putString(K_WAKE_WORDS, WakeWords.encode(words)) else remove(K_WAKE_WORDS)
        }.apply()
    }

    override fun trainedSha(name: String): String? = prefs.getString(K_TRAINED + name, null)

    override fun setTrainedSha(name: String, sha: String) {
        prefs.edit().putString(K_TRAINED + name, sha).apply()
    }

    override fun seen(record: String): RoomIdentity.Seen? {
        val raw = serials()[record] ?: return null
        val serial = raw.substringBefore(':').toLongOrNull() ?: return null
        return RoomIdentity.Seen(serial, raw.substringAfter(':', "").ifEmpty { null })
    }

    override fun remember(record: String, accepted: RoomIdentity.Accepted) {
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
        const val DEFAULT_THRESHOLD = 0.7f
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
        private const val K_WAKE_WORDS = "wake_words"
        private const val K_MUTED = "muted"
        private const val K_SERIALS = "manifest_serials"
        private const val K_APPROVED = "approved_once"
        private const val K_TRAINED = "trained_sha:"
        private const val K_REQUESTED = "requested_room"
        private const val K_ROOM_NAME = "room_name"
        private val SERIALS = MapSerializer(String.serializer(), String.serializer())

        /** 32 random bytes, URL-safe base64 (as `secrets.token_urlsafe(32)`). */
        fun newToken(random: SecureRandom = SecureRandom()): String =
            Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32).also { random.nextBytes(it) })

        /** Six digits, what the dashboard's approval card takes. */
        fun newApprovalCode(random: SecureRandom = SecureRandom()): String =
            (random.nextInt(900_000) + 100_000).toString()

        /** A wake threshold as the room keeps it: 0-1 (the dashboard's Wake
         *  sensitivity range, the Pi's `_wake_threshold_value`), else null. */
        fun thresholdValue(raw: Double?): Float? =
            raw?.takeIf { it in 0.0..1.0 }?.toFloat()

        /** The kept word list ([WakeWords.encode]) for the main word
         *  [main]: used when it starts with [main], else [main] alone (a
         *  list left from before a later push changed the main word). */
        fun keptWords(main: String, kept: String?): List<WakeWords.Word> {
            val words = WakeWords.decode(kept)
            return if (words != null && words[0].slug == main) words else listOf(WakeWords.Word(main))
        }

        /** Whether room mode is on, without building a store (MainActivity). */
        fun isActive(context: Context): Boolean =
            context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE).getBoolean(K_ACTIVE, false)
    }
}
