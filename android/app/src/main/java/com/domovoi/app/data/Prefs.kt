package com.domovoi.app.data

import android.content.Context
import android.util.Log
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.ServerIdentity
import com.domovoi.app.net.TokenHealth
import com.domovoi.app.player.LyricsNudge
import com.domovoi.app.ui.shell.PhoneBar
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
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
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
) : IdentityGate.PinStore, DeveloperOptions.Store, TokenHealth.RejectedBook {
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
    /** Servers (by normalised address) that refused the token this phone
     *  holds for them (net/TokenHealth.kt). No token or hash is kept here:
     *  any change to a server's token clears its entry. */
    private val kRejectedTokens = stringPreferencesKey("rejected_tokens")
    private val kTheme = stringPreferencesKey("theme_mode")
    private val kDeviceId = stringPreferencesKey("client_id")
    private val kListener = stringPreferencesKey("listener_person")
    private val kOwners = stringPreferencesKey("owner_people")
    private val kSharedScreens = stringPreferencesKey("shared_screens")
    private val kLyricsPanelOpen = booleanPreferencesKey("lyrics_panel_open")
    private val kLyricsSheetOpen = booleanPreferencesKey("lyrics_sheet_open")
    private val kLyricsRoomNudge = stringPreferencesKey("lyrics_room_nudge")
    private val kPreferLocal = booleanPreferencesKey("prefer_local")
    private val kPhoneFolders = stringPreferencesKey("phone_folders")
    private val kPhoneBar = stringPreferencesKey("phone_bar")
    private val kChatPlace = stringPreferencesKey("chat_place")
    private val kChatServerShareSeen = booleanPreferencesKey("chat_server_share_seen")
    private val kVoiceChoice = stringPreferencesKey("voice_choice")
    private val kVoiceInfo = stringPreferencesKey("voice_server_info")
    private val kDeveloperOptions = booleanPreferencesKey("developer_options")

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

    /** Servers whose answer said the token this phone holds for them is
     *  wrong (TokenHealth): the token is not sent there until it changes. */
    private val _rejectedTokens = MutableStateFlow<Set<String>>(emptySet())
    val rejectedTokens: StateFlow<Set<String>> = _rejectedTokens

    private val _themeMode = MutableStateFlow(ThemeMode.System)
    val themeMode: StateFlow<ThemeMode> = _themeMode

    private val _listenerPersonId = MutableStateFlow<String?>(null)
    val listenerPersonId: StateFlow<String?> = _listenerPersonId

    /** "This phone belongs to", per server (normalised address -> that
     *  server's `people.id`): who usually talks from this phone. Voice ID
     *  still decides who spoke; a voice message's speaker marker is drawn
     *  softer when it names someone else (ui/screens/chat/SpeakerMarker.kt).
     *  Read with [ServerCredentials.ownerFor]; absent: nobody in particular. */
    private val _owners = MutableStateFlow<Map<String, String>>(emptyMap())
    val owners: StateFlow<Map<String, String>> = _owners

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

    /** This phone's bottom-bar tabs as picked in Settings → Phone bar, as
     *  ui/shell/PhoneBar.kt encodes them; null = never picked (the
     *  default). The web keeps the same choice in localStorage. */
    private val _phoneBar = MutableStateFlow<String?>(null)
    val phoneBar: StateFlow<String?> = _phoneBar

    /** Where this phone last chatted (ui/screens/chat/ChatPlace.kt
     *  ChatPlaceKind.key: "phone" or "server"); null = never, so a new chat
     *  starts in the first default. */
    private val _chatPlace = MutableStateFlow<String?>(null)
    val chatPlace: StateFlow<String?> = _chatPlace

    /** This phone has sent a message in a home-server chat, so the chip no
     *  longer needs to say those chats are shared with the household. */
    private val _chatServerShareSeen = MutableStateFlow(false)
    val chatServerShareSeen: StateFlow<Boolean> = _chatServerShareSeen

    /** The phone's voice (Settings → Voice): "server" (the default: the
     *  server's voice) or a voice id (voice/PhoneVoice.kt). */
    private val _voiceChoice = MutableStateFlow("server")
    val voiceChoice: StateFlow<String> = _voiceChoice

    /** Each server's last `GET /api/voice` answer (its voice and "say it
     *  like" list), by normalised address, for when the phone is offline. */
    private var voiceInfo: Map<String, String> = emptyMap()

    /** Developer options as last set on this phone (data/DeveloperOptions.kt);
     *  null = never set, so the build decides. */
    @Volatile
    private var developerOptions: Boolean? = null

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
            _rejectedTokens.value = ServerCredentials.decodeTrusted(p[kRejectedTokens])
            _themeMode.value = runCatching { ThemeMode.valueOf(p[kTheme] ?: "System") }.getOrDefault(ThemeMode.System)
            _listenerPersonId.value = p[kListener]
            _owners.value = ServerCredentials.decodeOwners(p[kOwners])
            _sharedScreens.value = ServerCredentials.decodeSharedAnswers(p[kSharedScreens])
            _lyricsPanelOpen.value = p[kLyricsPanelOpen] ?: true
            _lyricsSheetOpen.value = p[kLyricsSheetOpen] ?: false
            _lyricsRoomNudge.value = LyricsNudge.decode(p[kLyricsRoomNudge])
            _preferLocal.value = p[kPreferLocal] ?: false
            _phoneFolders.value = PhoneFolders.decode(p[kPhoneFolders])
            // The retired Assistant tab moves to Chat, written back once
            // (PhoneBar.migrate).
            val savedBar = p[kPhoneBar]
            val bar = PhoneBar.migrate(savedBar)
            _phoneBar.value = bar
            if (bar != null && bar != savedBar) scope.launch { context.dataStore.edit { it[kPhoneBar] = bar } }
            _chatPlace.value = p[kChatPlace]
            _chatServerShareSeen.value = p[kChatServerShareSeen] ?: false
            _voiceChoice.value = p[kVoiceChoice] ?: "server"
            voiceInfo = decodeVoiceInfo(p[kVoiceInfo])
            developerOptions = p[kDeveloperOptions]
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
        // A new token (or none) is not the one the server refused.
        clearTokenRejected(url)
        _deviceToken.value = ServerCredentials.tokenFor(next, _serverUrl.value)
        scope.launch { vault.write(next) }
    }

    // ── A token the server refused (TokenHealth.RejectedBook) ──────────

    override fun isTokenRejected(url: String): Boolean =
        ServerCredentials.normalize(url) in _rejectedTokens.value

    /** Only while a token is held for [url]: "refused" describes that token. */
    override fun markTokenRejected(url: String) {
        if (ServerCredentials.tokenFor(deviceTokens, url) == null) return
        setRejected(ServerCredentials.withTrusted(_rejectedTokens.value, url))
    }

    /** Send the held token to [url] again (the card's "check again", or a
     *  new token replacing it). */
    fun clearTokenRejected(url: String) {
        if (!isTokenRejected(url)) return
        setRejected(ServerCredentials.withoutTrusted(_rejectedTokens.value, url))
    }

    private fun setRejected(next: Set<String>) {
        _rejectedTokens.value = next
        scope.launch { context.dataStore.edit { it[kRejectedTokens] = ServerCredentials.encodeTrusted(next) } }
    }

    /** The token issued by the server at [url] (by its saved spelling), or
     *  null. What the interceptor asks, with the base it scoped the
     *  request to, so a request intercepted between a switch's two writes
     *  can never pair the new address with the old household's token. */
    fun tokenForServer(url: String): String? = ServerCredentials.tokenFor(deviceTokens, url)

    fun isPaired(): Boolean = !_deviceToken.value.isNullOrBlank()

    /** A household token for ANY server is on this phone (room mode then
     *  needs a screen lock: roommode/RoomLockRules.kt). */
    fun holdsHouseholdToken(): Boolean = deviceTokens.values.any { it.isNotBlank() }

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

    /**
     * Pin [pin] for [url] IN PLACE OF the one pinned: the person said yes to
     * "this looks like a different Domovoi, or it was reinstalled" with
     * both fingerprints in view (ui/shell/IdentityChanged.kt). The one way
     * a pin changes without forgetting the server, and never without that
     * yes. The household token is DROPPED with the old pin: it belongs to
     * the identity it was paired with, and must not go to whatever holds the
     * new key (review S-T1, 2026-10-17). A reinstall is a new household and
     * would refuse it anyway, so the phone goes on to pairing.
     */
    fun replacePin(url: String, pin: ServerIdentity.Pin) {
        val key = IdentityGate.pinKey(url) ?: return
        setDeviceTokenFor(url, null)
        pin(key, pin)
        setTrustDecided(trustDecided + key)
    }

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
        setOwners(ServerCredentials.withOwner(_owners.value, url, null))
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

    /** [encoded] null = back to the default bar. */
    fun setPhoneBar(encoded: String?) {
        if (_phoneBar.value == encoded) return
        _phoneBar.value = encoded
        scope.launch {
            context.dataStore.edit { if (encoded == null) it.remove(kPhoneBar) else it[kPhoneBar] = encoded }
        }
    }

    fun setChatPlace(key: String) {
        if (_chatPlace.value == key) return
        _chatPlace.value = key
        scope.launch { context.dataStore.edit { it[kChatPlace] = key } }
    }

    fun setChatServerShareSeen() {
        if (_chatServerShareSeen.value) return
        _chatServerShareSeen.value = true
        scope.launch { context.dataStore.edit { it[kChatServerShareSeen] = true } }
    }

    fun setVoiceChoice(choice: String) {
        if (_voiceChoice.value == choice) return
        _voiceChoice.value = choice
        scope.launch { context.dataStore.edit { it[kVoiceChoice] = choice } }
    }

    /** The cached `/api/voice` answer of the server at [url], or null. */
    fun voiceInfoFor(url: String): String? =
        ServerCredentials.normalize(url).takeIf { it.isNotBlank() }?.let { voiceInfo[it] }

    fun setVoiceInfo(url: String, json: String) {
        val key = ServerCredentials.normalize(url)
        if (key.isBlank() || voiceInfo[key] == json) return
        val next = voiceInfo + (key to json)
        voiceInfo = next
        scope.launch { context.dataStore.edit { it[kVoiceInfo] = encodeVoiceInfo(next) } }
    }

    /** Who this phone belongs to on the server at [url]; null: nobody. */
    fun setOwnerPersonId(url: String, id: String?) {
        setOwners(ServerCredentials.withOwner(_owners.value, url, id))
    }

    private fun setOwners(next: Map<String, String>) {
        if (next == _owners.value) return
        _owners.value = next
        scope.launch { context.dataStore.edit { it[kOwners] = ServerCredentials.encodeOwners(next) } }
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

    // ── Developer options (DeveloperOptions.Store) ─────────────────────

    override fun loadDeveloperOptions(): Boolean? = developerOptions

    override fun saveDeveloperOptions(on: Boolean) {
        developerOptions = on
        scope.launch { context.dataStore.edit { it[kDeveloperOptions] = on } }
    }

    private companion object {
        const val TAG = "Prefs"

        private val voiceInfoSerializer = MapSerializer(String.serializer(), String.serializer())

        fun decodeVoiceInfo(raw: String?): Map<String, String> =
            raw?.let { runCatching { Json.decodeFromString(voiceInfoSerializer, it) }.getOrNull() } ?: emptyMap()

        fun encodeVoiceInfo(map: Map<String, String>): String = Json.encodeToString(voiceInfoSerializer, map)
    }
}
