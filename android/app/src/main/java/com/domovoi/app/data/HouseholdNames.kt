package com.domovoi.app.data

import android.content.Context
import android.content.SharedPreferences

/**
 * The household's room names and the picked listener's name, as this phone
 * last saw them on its own screens (Home's rooms, Music's now-playing, the
 * people lists in Settings, Audiobooks and Podcasts), kept per server. The
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

    /** The rooms [server] reported, in the order it gave them. */
    fun rooms(server: String): List<String> =
        store.get(key(server, ROOMS)).orEmpty().split('\n').map { it.trim() }.filter { it.isNotEmpty() }

    /** Remembers [rooms] for [server]; an empty answer is kept too (no rooms
     *  now). Names are cut to a sane length and count. */
    fun recordRooms(server: String, rooms: List<String>) {
        if (server.isBlank()) return
        val clean = rooms.map { it.replace('\n', ' ').trim().take(MAX_NAME) }.filter { it.isNotEmpty() }.distinct().take(MAX_ROOMS)
        store.put(key(server, ROOMS), clean.joinToString("\n"))
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
        private const val ROOMS = "rooms"
        private const val LISTENER = "listener"
        private const val MAX_NAME = 60
        private const val MAX_ROOMS = 40

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
