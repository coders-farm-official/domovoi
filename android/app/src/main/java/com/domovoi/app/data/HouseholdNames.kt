package com.domovoi.app.data

import android.content.Context
import android.content.SharedPreferences

/**
 * The picked listener's name, as this phone last saw it on its own screens
 * (the people lists in Settings, Audiobooks and Podcasts), kept per server.
 * (No room names: phone chats answer about the phone only, owner
 * 2026-10-21.) The
 * phone chat's prompt reads them from here (assistant/PhoneFacts.kt): no
 * request is ever made to fill them, so a phone chat never reaches the
 * server, and away from home they are simply what was seen last.
 *
 * Small and app-private (SharedPreferences `household_names`, outside
 * backups like the rest of the app's storage).
 */
class HouseholdNames(private val store: KeyValues) {

    /** The storage it sits on: SharedPreferences in the app, a map in tests. */
    interface KeyValues {
        fun get(key: String): String?
        fun put(key: String, value: String?)
    }

    /** The name of [personId] on [server], when this phone has seen it. */
    fun listenerName(server: String, personId: String?): String? {
        if (personId.isNullOrBlank()) return null
        val stored = store.get(key(server, LISTENER)) ?: return null
        val id = stored.substringBefore('\n')
        return stored.substringAfter('\n', "").takeIf { id == personId && it.isNotBlank() }
    }

    fun recordListener(server: String, personId: String?, name: String?) {
        if (server.isBlank() || personId.isNullOrBlank() || name.isNullOrBlank()) return
        store.put(key(server, LISTENER), personId + "\n" + name.replace('\n', ' ').trim().take(MAX_NAME))
    }

    private fun key(server: String, what: String) = "$what@${server.trim().trimEnd('/').lowercase()}"

    companion object {
        private const val LISTENER = "listener"
        private const val MAX_NAME = 60

        fun forApp(context: Context): HouseholdNames {
            val prefs: SharedPreferences = context.applicationContext.getSharedPreferences("household_names", Context.MODE_PRIVATE)
            return HouseholdNames(object : KeyValues {
                override fun get(key: String): String? = prefs.getString(key, null)
                override fun put(key: String, value: String?) {
                    prefs.edit().apply { if (value == null) remove(key) else putString(key, value) }.apply()
                }
            })
        }
    }
}
