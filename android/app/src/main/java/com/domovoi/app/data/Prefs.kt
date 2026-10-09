package com.domovoi.app.data

import android.content.Context
import android.util.Log
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.ServerIdentity
import com.domovoi.app.player.LyricsNudge
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
 *
 * What is kept where: everything but the household tokens in the plain
 * Preferences DataStore `domovoi`; the tokens in the [TokenVault] (sealed
 * under an Android Keystore key, A6-04). A plain `device_tokens` value
 * found in the DataStore at start — an install from before the vault, or
 * the test harness pairing the emulator — is swept into the vault and
 * removed.
 */
class Prefs(
    private val context: Context,
    private val vault: TokenVault = TokenVault(PrefsVaultStore(context), KeystoreSealer()) { Log.w(TAG, it) },
) : IdentityGate.PinStore {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val kServer = stringPreferencesKey("server_url")
    private val kServers = stringPreferencesKey("known_servers")
    private val kTrusted = stringPreferencesKey("trusted_servers")
    /** The pre-vault home of the tokens; read once at start and removed. */
    private val kDeviceTokens = stringPreferencesKey("device_tokens")
    private val kIdentityPins = stringPreferencesKey("identity_pins")
    /** Servers (by pin key) whose trust decision was taken with the identity
     *  in view — pinned from the dialog, or recorded as offering none. */
    private val kTrustDecided = stringPreferencesKey("trust_decisions")
    private val kTheme = stringPreferencesKey("theme_mode")
    private val kDeviceId = stringPreferencesKey("client_id")
    private val kListener = stringPreferencesKey("listener_person")
    private val kSharedScreens = stringPreferencesKey("shared_screens")
    private val kLyricsPanelOpen = booleanPreferencesKey("lyrics_panel_open")
    private val kLyricsSheetOpen = booleanPreferencesKey("lyrics_sheet_open")
    private val kLyricsRoomNudge = stringPreferencesKey("lyrics_room_nudge")
    private val kPreferLocal = booleanPreferencesKey("prefer_local")
    private val kPhoneFolders = stringPreferencesKey("phone_folders")

    private val _serverUrl = MutableStateFlow("")
    val serverUrl: StateFlow<String> = _serverUrl

    private val _knownServers = MutableStateFlow<List<KnownServer>>(emptyList())
    val knownServers: StateFlow<List<KnownServer>> = _knownServers

    /** Servers the user confirmed in the picker (ServerCredentials). */
    private val _trustedServers = MutableStateFlow<Set<String>>(emptySet())
    val trustedServers: StateFlow<Set<String>> = _trustedServers

    /** Household device token of the ACTIVE server; null = not paired yet. */
    private val _deviceToken = MutableStateFlow<String?>(null)
    val deviceToken: StateFlow<String?> = _deviceToken
    private var deviceTokens: Map<String, String> = emptyMap()

    /** Each server's pinned identity, by [IdentityGate.pinKey] (A6-03). */
    private val _identityPins = MutableStateFlow<Map<String, ServerIdentity.Pin>>(emptyMap())
    val identityPins: StateFlow<Map<String, ServerIdentity.Pin>> = _identityPins

    @Volatile
    private var trustDecided: Set<String> = emptySet()

    private val _themeMode = MutableStateFlow(ThemeMode.System)
    val themeMode: StateFlow<ThemeMode> = _themeMode

    private val _listenerPersonId = MutableStateFlow<String?>(null)
    val listenerPersonId: StateFlow<String?> = _listenerPersonId

    /**
     * Each server's last answer to "is this install a shared screen?"
     * (the `shared_screen` on this device's own row), keyed by server URL.
     * The web keeps the same answer in localStorage so a reload paints the
     * shared view at once instead of flashing personal content while the
     * registration is in flight; this is that, per server. A server with no
     * entry has not answered yet — see net/SharedScreen.kt.
     */
    private val _sharedScreens = MutableStateFlow<Map<String, Boolean>>(emptyMap())
    val sharedScreens: StateFlow<Map<String, Boolean>> = _sharedScreens

    /** Whether the player tab's "lyrics" section is open (open until closed;
     *  the web's localStorage `domovoi-lyrics-panel-open`). */
    private val _lyricsPanelOpen = MutableStateFlow(true)
    val lyricsPanelOpen: StateFlow<Boolean> = _lyricsPanelOpen

    /** Whether the player sheet's "lyrics" section is open (closed until
     *  opened; the web's `domovoi-lyrics-sheet-open`). */
    private val _lyricsSheetOpen = MutableStateFlow(false)
    val lyricsSheetOpen: StateFlow<Boolean> = _lyricsSheetOpen

    /** Each room's lyrics timing nudge, room → ms (positive = lyrics later,
     *  ±10 000; [LyricsNudge]); the web keeps one per room in localStorage. */
    private val _lyricsRoomNudge = MutableStateFlow<Map<String, Long>>(emptyMap())
    val lyricsRoomNudge: StateFlow<Map<String, Long>> = _lyricsRoomNudge

    /** The connection dialog's "this phone" choice: stay on the phone's own
     *  media even while the saved server answers, until the server is picked
     *  again (ui/shell/ShellMode.kt). */
    private val _preferLocal = MutableStateFlow(false)
    val preferLocal: StateFlow<Boolean> = _preferLocal

    /** Folders added to the phone's Files tab (data/PhoneFolders.kt). */
    private val _phoneFolders = MutableStateFlow<List<PhoneFolder>>(emptyList())
    val phoneFolders: StateFlow<List<PhoneFolder>> = _phoneFolders

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
            // An install from before the trust list existed only ever saved a
            // server because the user picked it by hand, so those count as
            // trusted rather than being asked about again.
            _trustedServers.value = p[kTrusted]?.let { ServerCredentials.decodeTrusted(it) }
                ?: (_knownServers.value.map { ServerCredentials.normalize(it.url) }.toSet() +
                    setOfNotNull(_serverUrl.value.takeIf { it.isNotBlank() })).also { seeded ->
                    scope.launch {
                        context.dataStore.edit { it[kTrusted] = ServerCredentials.encodeTrusted(seeded) }
                    }
                }
            deviceTokens = vault.read()
            // The sweep: a plain `device_tokens` value is moved into the
            // vault, whoever wrote it, and the plain key goes.
            val plain = p[kDeviceTokens]
            if (plain != null) {
                val legacy = ServerCredentials.decodeTokens(plain)
                var moved = true
                if (legacy.isNotEmpty()) {
                    deviceTokens = TokenVault.merged(deviceTokens, legacy)
                    moved = vault.write(deviceTokens)
                    if (moved) Log.i(TAG, "moved ${legacy.size} household token(s) into the vault")
                }
                // The plain key goes only once the vault holds the tokens: a
                // phone whose Keystore will not seal keeps them where they
                // were (the vault said so in the log) rather than losing them.
                if (moved) scope.launch { context.dataStore.edit { it.remove(kDeviceTokens) } }
            }
            _deviceToken.value = ServerCredentials.tokenFor(deviceTokens, _serverUrl.value)
            _identityPins.value = ServerIdentity.decodePins(p[kIdentityPins])
            trustDecided = ServerCredentials.decodeTrusted(p[kTrustDecided])
            _themeMode.value = runCatching { ThemeMode.valueOf(p[kTheme] ?: "System") }.getOrDefault(ThemeMode.System)
            _listenerPersonId.value = p[kListener]
            _sharedScreens.value = ServerCredentials.decodeSharedAnswers(p[kSharedScreens])
            _lyricsPanelOpen.value = p[kLyricsPanelOpen] ?: true
            _lyricsSheetOpen.value = p[kLyricsSheetOpen] ?: false
            _lyricsRoomNudge.value = LyricsNudge.decode(p[kLyricsRoomNudge])
            _preferLocal.value = p[kPreferLocal] ?: false
            _phoneFolders.value = PhoneFolders.decode(p[kPhoneFolders])
            deviceId = p[kDeviceId] ?: ("android-" + Random.nextInt(0x10000).toString(16).padStart(4, '0')).also { id ->
                scope.launch { context.dataStore.edit { it[kDeviceId] = id } }
            }
        }
    }

    /**
     * Point the app at [url]. Refused for a server the user has not trusted
     * (nothing written, `false` returned) — the picker shows the address and
     * asks first, then calls [trustServer].
     *
     * Switching also drops trust that belongs to nothing any more: an
     * address trusted but never listed as a known server (the Connection
     * panel used to do that) has no row and no forget button, and would
     * otherwise be reused silently the next time a sweep found something at
     * it (P2-at-01). Only the TRUST goes — not the token or the pin: a
     * harness-paired or older install whose server was never listed would
     * otherwise be unpaired from it by the next switch (P2-at-01 review).
     * The one being switched to is never pruned.
     */
    fun setServerUrl(url: String): Boolean {
        val clean = ServerCredentials.normalize(url)
        if (clean.isNotBlank() && !isTrusted(clean)) return false
        _serverUrl.value = clean
        _deviceToken.value = ServerCredentials.tokenFor(deviceTokens, clean)
        scope.launch { context.dataStore.edit { it[kServer] = clean } }
        ServerCredentials.orphanTrust(_trustedServers.value, _knownServers.value.map { it.url }, clean)
            .forEach { untrustServer(it) }
        return true
    }

    // ── Trust ──────────────────────────────────────────────────────────

    fun isTrusted(url: String): Boolean =
        ServerCredentials.isTrusted(_trustedServers.value, url)

    /**
     * Trust [url], with the [identity] the trust dialog (or the Connection
     * panel's probe) showed. That identity becomes the pin — the first
     * proof has to match it, not whoever answers first on some network —
     * unless one is pinned already (a pin changes only by forgetting the
     * server). A server that advertised none is recorded as such, so it
     * is not pinned later behind the user's back
     * ([mayPinOnFirstProof]; A6-03 review).
     */
    fun trustServer(url: String, identity: ServerIdentity.Pin? = null) {
        setTrusted(ServerCredentials.withTrusted(_trustedServers.value, url))
        val key = IdentityGate.pinKey(url) ?: return
        if (identity != null && pinFor(key) == null) pin(key, identity)
        setTrustDecided(trustDecided + key)
    }

    fun untrustServer(url: String) = setTrusted(ServerCredentials.withoutTrusted(_trustedServers.value, url))

    private fun setTrusted(next: Set<String>) {
        _trustedServers.value = next
        scope.launch {
            context.dataStore.edit { it[kTrusted] = ServerCredentials.encodeTrusted(next) }
        }
    }

    // ── Household device token ─────────────────────────────────────────

    /** Store (or, with a blank value, forget) the active server's token. */
    fun setDeviceToken(token: String?) = setDeviceTokenFor(_serverUrl.value, token)

    fun setDeviceTokenFor(url: String, token: String?) {
        val next = ServerCredentials.withToken(deviceTokens, url, token)
        deviceTokens = next
        _deviceToken.value = ServerCredentials.tokenFor(next, _serverUrl.value)
        scope.launch { vault.write(next) }
    }

    /** The token issued by the server at [url] (by its saved spelling), or
     *  null. What the interceptor asks, with the base it scoped the
     *  request to, so a request intercepted between a switch's two writes
     *  can never pair the new address with the old household's token. */
    fun tokenForServer(url: String): String? = ServerCredentials.tokenFor(deviceTokens, url)

    fun isPaired(): Boolean = !_deviceToken.value.isNullOrBlank()

    // ── Server identity pins (IdentityGate.PinStore) ───────────────────

    override fun pinFor(key: String): ServerIdentity.Pin? = _identityPins.value[key]

    override fun pin(key: String, pin: ServerIdentity.Pin) = setPins(_identityPins.value + (key to pin))

    /** Only a server trusted before the dialog pinned identities (an
     *  upgraded install) may be pinned from its first proof. */
    override fun mayPinOnFirstProof(key: String): Boolean = key !in trustDecided

    private fun setTrustDecided(next: Set<String>) {
        trustDecided = next
        scope.launch { context.dataStore.edit { it[kTrustDecided] = ServerCredentials.encodeTrusted(next) } }
    }

    /** The identity pinned for a saved server address, if any. */
    fun pinForServer(url: String): ServerIdentity.Pin? = IdentityGate.pinKey(url)?.let(::pinFor)

    fun clearPinFor(url: String) {
        val key = IdentityGate.pinKey(url) ?: return
        if (key in _identityPins.value) setPins(_identityPins.value - key)
    }

    private fun setPins(next: Map<String, ServerIdentity.Pin>) {
        _identityPins.value = next
        scope.launch { context.dataStore.edit { it[kIdentityPins] = ServerIdentity.encodePins(next) } }
    }

    // ── Known servers ──────────────────────────────────────────────────

    fun upsertKnownServer(url: String, name: String? = null) {
        val clean = url.trim().trimEnd('/')
        if (clean.isBlank()) return
        val kept = _knownServers.value.filter { it.url != clean }
        // Keep an existing name if the new sighting didn't resolve one.
        val existing = _knownServers.value.firstOrNull { it.url == clean }?.name
        setKnownServers(kept + KnownServer(clean, name ?: existing))
    }

    /** Forget a server completely: its entry, its trust, its token, its
     *  pinned identity and whether it called this install a shared screen. */
    fun removeKnownServer(url: String) {
        setKnownServers(_knownServers.value.filter { it.url != url })
        forgetServer(url)
    }

    /**
     * Forget the ACTIVE server: the app is left with no server (the shell
     * returns to the server list) and the server's row, trust, token,
     * pinned identity and trust decision all go. The way out after a
     * legitimate identity change — a reinstalled core, new hardware at the
     * same address — which the shell would otherwise report as "did not
     * prove it is the Domovoi this phone paired with" for good, since
     * neither list could forget the server in use (P2-at-01 review).
     * Returns the address forgotten, or null when there was none.
     */
    fun forgetActiveServer(): String? {
        val url = _serverUrl.value.takeIf { it.isNotBlank() } ?: return null
        _serverUrl.value = ""
        _deviceToken.value = null
        scope.launch { context.dataStore.edit { it[kServer] = "" } }
        removeKnownServer(url)
        return url
    }

    /** Everything remembered ABOUT [url] (not its known-server row). The
     *  pin is shared by every spelling of one server, so another spelling
     *  of the ACTIVE server keeps it ([ServerCredentials.clearsPinOf]). */
    private fun forgetServer(url: String) {
        untrustServer(url)
        setDeviceTokenFor(url, null)
        if (ServerCredentials.clearsPinOf(url, _serverUrl.value)) {
            clearPinFor(url)
            IdentityGate.pinKey(url)?.let { key -> if (key in trustDecided) setTrustDecided(trustDecided - key) }
        }
        setSharedAnswers(_sharedScreens.value - ServerCredentials.normalize(url))
    }

    // ── Shared screen ──────────────────────────────────────────────────

    /** Record [url]'s answer; a no-op when it has not changed, because the
     *  shell re-asks every couple of minutes and the answer rarely moves. */
    fun setSharedScreen(url: String, shared: Boolean) {
        val clean = ServerCredentials.normalize(url)
        if (clean.isBlank() || _sharedScreens.value[clean] == shared) return
        setSharedAnswers(_sharedScreens.value + (clean to shared))
    }

    private fun setSharedAnswers(next: Map<String, Boolean>) {
        _sharedScreens.value = next
        scope.launch {
            context.dataStore.edit { it[kSharedScreens] = ServerCredentials.encodeSharedAnswers(next) }
        }
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

    fun setPreferLocal(local: Boolean) {
        if (_preferLocal.value == local) return
        _preferLocal.value = local
        scope.launch { context.dataStore.edit { it[kPreferLocal] = local } }
    }

    fun setPhoneFolders(list: List<PhoneFolder>) {
        if (_phoneFolders.value == list) return
        _phoneFolders.value = list
        scope.launch { context.dataStore.edit { it[kPhoneFolders] = PhoneFolders.encode(list) } }
    }

    fun setListenerPersonId(id: String?) {
        _listenerPersonId.value = id
        scope.launch {
            context.dataStore.edit {
                if (id == null) it.remove(kListener) else it[kListener] = id
            }
        }
    }

    // ── Lyrics ─────────────────────────────────────────────────────────

    fun setLyricsPanelOpen(open: Boolean) {
        _lyricsPanelOpen.value = open
        scope.launch { context.dataStore.edit { it[kLyricsPanelOpen] = open } }
    }

    fun setLyricsSheetOpen(open: Boolean) {
        _lyricsSheetOpen.value = open
        scope.launch { context.dataStore.edit { it[kLyricsSheetOpen] = open } }
    }

    /** Set [room]'s lyrics nudge (clamped to ±10 000 ms; zero forgets it). */
    fun setLyricsRoomNudge(room: String, ms: Long) {
        val next = LyricsNudge.with(_lyricsRoomNudge.value, room, ms)
        if (next == _lyricsRoomNudge.value) return
        _lyricsRoomNudge.value = next
        scope.launch { context.dataStore.edit { it[kLyricsRoomNudge] = LyricsNudge.encode(next) } }
    }

    private companion object {
        const val TAG = "Prefs"
    }
}
