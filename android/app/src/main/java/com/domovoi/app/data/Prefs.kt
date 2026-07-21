package com.domovoi.app.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.domovoi.app.ui.theme.ThemeMode
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlin.random.Random

private val Context.dataStore by preferencesDataStore(name = "domovoi")

/** A saved domovoi/dashboard endpoint the user can switch between. */
@Serializable
data class KnownServer(val url: String, val name: String? = null)

/**
 * App-level settings. Mirrors the web's localStorage keys:
 * domovoi-theme, domovoi-client-id, domovoi-listener-person — plus the server
 * base URL, which the browser gets for free from location.origin.
 */
class Prefs(private val context: Context) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val kServer = stringPreferencesKey("server_url")
    private val kServers = stringPreferencesKey("known_servers")
    private val kTheme = stringPreferencesKey("theme_mode")
    private val kDeviceId = stringPreferencesKey("client_id")
    private val kListener = stringPreferencesKey("listener_person")

    private val _serverUrl = MutableStateFlow("")
    val serverUrl: StateFlow<String> = _serverUrl

    private val _knownServers = MutableStateFlow<List<KnownServer>>(emptyList())
    val knownServers: StateFlow<List<KnownServer>> = _knownServers

    private val _themeMode = MutableStateFlow(ThemeMode.System)
    val themeMode: StateFlow<ThemeMode> = _themeMode

    private val _listenerPersonId = MutableStateFlow<String?>(null)
    val listenerPersonId: StateFlow<String?> = _listenerPersonId

    /** Stable per-install client id, e.g. "android-4f21" (web: "browser-xxxx"). */
    var deviceId: String = ""
        private set

    init {
        // Small blocking read at process start keeps everything downstream simple.
        runBlocking {
            val p = context.dataStore.data.first()
            _serverUrl.value = p[kServer] ?: ""
            _knownServers.value = runCatching {
                Json.decodeFromString(ListSerializer(KnownServer.serializer()), p[kServers] ?: "[]")
            }.getOrDefault(emptyList())
            _themeMode.value = runCatching { ThemeMode.valueOf(p[kTheme] ?: "System") }.getOrDefault(ThemeMode.System)
            _listenerPersonId.value = p[kListener]
            deviceId = p[kDeviceId] ?: ("android-" + Random.nextInt(0x10000).toString(16).padStart(4, '0')).also { id ->
                scope.launch { context.dataStore.edit { it[kDeviceId] = id } }
            }
        }
    }

    fun setServerUrl(url: String) {
        val clean = url.trim().trimEnd('/')
        _serverUrl.value = clean
        scope.launch { context.dataStore.edit { it[kServer] = clean } }
    }

    fun upsertKnownServer(url: String, name: String? = null) {
        val clean = url.trim().trimEnd('/')
        if (clean.isBlank()) return
        val kept = _knownServers.value.filter { it.url != clean }
        // Keep an existing name if the new sighting didn't resolve one.
        val existing = _knownServers.value.firstOrNull { it.url == clean }?.name
        setKnownServers(kept + KnownServer(clean, name ?: existing))
    }

    fun removeKnownServer(url: String) {
        setKnownServers(_knownServers.value.filter { it.url != url })
    }

    private fun setKnownServers(list: List<KnownServer>) {
        _knownServers.value = list
        scope.launch {
            context.dataStore.edit {
                it[kServers] = Json.encodeToString(ListSerializer(KnownServer.serializer()), list)
            }
        }
    }

    /** Display label for the active server: its saved name, else host:port. */
    fun serverLabel(): String {
        val url = _serverUrl.value
        if (url.isBlank()) return "no server"
        val known = _knownServers.value.firstOrNull { it.url == url }?.name
        if (!known.isNullOrBlank()) return known
        return url.removePrefix("http://").removePrefix("https://")
    }

    fun setThemeMode(mode: ThemeMode) {
        _themeMode.value = mode
        scope.launch { context.dataStore.edit { it[kTheme] = mode.name } }
    }

    fun setListenerPersonId(id: String?) {
        _listenerPersonId.value = id
        scope.launch {
            context.dataStore.edit {
                if (id == null) it.remove(kListener) else it[kListener] = id
            }
        }
    }
}
