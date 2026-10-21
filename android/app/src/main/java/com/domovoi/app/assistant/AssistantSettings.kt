package com.domovoi.app.assistant

import android.content.Context

/**
 * The on-device assistant's own choices on this phone. Kept apart from the
 * app's Prefs (a small SharedPreferences file of its own, `assistant`), app
 * private and outside backups like the rest of the app's storage.
 */
interface AssistantSettings {
    /** Where the model runs. CPU unless the owner turns the GPU on (owner
     *  decision 2026-10-12: CPU by default). */
    var backend: LlmBackend

    /** Off: the download waits for Wi-Fi (the default). */
    var allowMobileData: Boolean

    /** The conversation on screen, to come back to after a restart. */
    var currentConversation: String?

    /** When the running download was started, epoch ms (for "downloaded in"). */
    var downloadStartedAt: Long?

    /** How long the last finished download took, ms. */
    var lastDownloadMs: Long?

    /** Read typed questions' replies aloud too. Off (plan decision 7):
     *  replies to spoken questions are spoken, typed ones stay silent. */
    var speakTyped: Boolean

    /** "Always allow internet" for phone chats' lookups (part 5, W1). Off
     *  (owner decision 2026-10-20): each lookup asks first. */
    var alwaysAllowInternet: Boolean

    /** The engine's measured speeds, per backend (PhoneDocJobs.addRow):
     *  what a long document's time estimate comes from. */
    var speedRows: String
}

/** In memory only: for tests. */
class MemoryAssistantSettings(
    override var backend: LlmBackend = LlmBackend.CPU,
    override var allowMobileData: Boolean = false,
    override var currentConversation: String? = null,
    override var downloadStartedAt: Long? = null,
    override var lastDownloadMs: Long? = null,
    override var speakTyped: Boolean = false,
    override var alwaysAllowInternet: Boolean = false,
    override var speedRows: String = "",
) : AssistantSettings

/** The app's: SharedPreferences `assistant`. */
class PhoneAssistantSettings(context: Context) : AssistantSettings {
    private val prefs = context.applicationContext.getSharedPreferences("assistant", Context.MODE_PRIVATE)

    override var backend: LlmBackend
        get() = if (prefs.getString(K_BACKEND, null) == LlmBackend.GPU.name) LlmBackend.GPU else LlmBackend.CPU
        set(value) = prefs.edit().putString(K_BACKEND, value.name).apply()

    override var allowMobileData: Boolean
        get() = prefs.getBoolean(K_MOBILE, false)
        set(value) = prefs.edit().putBoolean(K_MOBILE, value).apply()

    override var currentConversation: String?
        get() = prefs.getString(K_CURRENT, null)
        set(value) = prefs.edit().putString(K_CURRENT, value).apply()

    override var downloadStartedAt: Long?
        get() = prefs.getLong(K_STARTED, -1).takeIf { it >= 0 }
        set(value) = prefs.edit().apply { if (value == null) remove(K_STARTED) else putLong(K_STARTED, value) }.apply()

    override var lastDownloadMs: Long?
        get() = prefs.getLong(K_TOOK, -1).takeIf { it >= 0 }
        set(value) = prefs.edit().apply { if (value == null) remove(K_TOOK) else putLong(K_TOOK, value) }.apply()

    override var speakTyped: Boolean
        get() = prefs.getBoolean(K_SPEAK_TYPED, false)
        set(value) = prefs.edit().putBoolean(K_SPEAK_TYPED, value).apply()

    override var alwaysAllowInternet: Boolean
        get() = prefs.getBoolean(K_ALWAYS_INTERNET, false)
        set(value) = prefs.edit().putBoolean(K_ALWAYS_INTERNET, value).apply()

    override var speedRows: String
        get() = prefs.getString(K_SPEEDS, null).orEmpty()
        set(value) = prefs.edit().putString(K_SPEEDS, value).apply()

    private companion object {
        const val K_ALWAYS_INTERNET = "always_allow_internet"
        const val K_SPEEDS = "measured_speeds"
        const val K_SPEAK_TYPED = "speak_typed_replies"
        const val K_BACKEND = "backend"
        const val K_MOBILE = "allow_mobile_data"
        const val K_CURRENT = "current_conversation"
        const val K_STARTED = "download_started_at"
        const val K_TOOK = "last_download_ms"
    }
}
