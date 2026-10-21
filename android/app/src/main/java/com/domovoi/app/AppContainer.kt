package com.domovoi.app

import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.SystemClock
import android.util.Log
import androidx.compose.runtime.staticCompositionLocalOf
import com.domovoi.app.net.ReportUpload
import com.domovoi.app.alerts.HelpAlerts
import com.domovoi.app.alerts.IncidentAlerts
import com.domovoi.app.assistant.AssistantBench
import com.domovoi.app.assistant.AssistantController
import com.domovoi.app.assistant.AppPhoneContext
import com.domovoi.app.assistant.AssistantSettings
import com.domovoi.app.assistant.AssistantSupport
import com.domovoi.app.assistant.ModelDownloader
import com.domovoi.app.assistant.PhoneContext
import com.domovoi.app.assistant.PhoneTimer
import com.domovoi.app.data.HouseholdNames
import com.domovoi.app.alerts.TimerAlerts
import com.domovoi.app.data.DeveloperOptions
import com.domovoi.app.data.Prefs
import com.domovoi.app.audiobooks.ApiPositionRemote
import com.domovoi.app.audiobooks.BookDownloads
import com.domovoi.app.audiobooks.BookStore
import com.domovoi.app.audiobooks.PositionStore
import com.domovoi.app.audiobooks.PositionSync
import com.domovoi.app.audiobooks.SavedShelf
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.HouseholdLock
import com.domovoi.app.net.DeviceAuthInterceptor
import com.domovoi.app.net.DeviceDownloads
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.NetworkWatch
import com.domovoi.app.net.PhoneUploads
import com.domovoi.app.net.StateBus
import com.domovoi.app.net.TokenHealth
import com.domovoi.app.ui.screens.manual.ManualRepository
import com.domovoi.app.player.LyricsRepository
import com.domovoi.app.player.PlayerController
import com.domovoi.app.player.ServerScope
import com.domovoi.app.voice.PhoneVoice
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import java.util.concurrent.TimeUnit

/** Process-wide singletons. Deliberately no DI framework — one small graph. */
class AppContainer(context: Context) {
    val prefs = Prefs(context)

    /** The networks the phone is on, as one fingerprint every identity
     *  verdict is keyed to (net/NetworkWatch.kt). Registered with
     *  ConnectivityManager by DomovoiApplication. A change also cancels
     *  any save-to-device download still carrying the household token
     *  (net/DeviceDownloads.kt). */
    val network = NetworkWatch(
        onChange = {
            identity.networkChanged()
            DeviceDownloads.networkChanged(context)
            // A new network is most often home again: books waiting to
            // save try now, and places set while away go up
            // (audiobooks/BookDownloads.kt, BookPositions.kt).
            booksReconnected(context)
        },
        log = { Log.i("NetworkWatch", it) },
    )

    /** The saved server proves its identity on each network before the
     *  household token goes to it (net/IdentityGate.kt, A6-03). Its probe
     *  runs on a token-less copy of the app's client; the pins live in
     *  Prefs. */
    val identity: IdentityGate = IdentityGate(
        probe = { base, challenge ->
            IdentityGate.httpProbe(Discovery.client(api.http, IdentityGate.PROBE_TIMEOUT_MS), base, challenge)
        },
        pins = prefs,
        network = { network.fingerprint },
        log = { Log.i("IdentityGate", it) },
    )
    /** What the server's answers said about this phone's household token
     *  (net/TokenHealth.kt): a refused token is not sent again (kept in
     *  Prefs), and a server's "wait" (429 Retry-After) is honoured before
     *  anything carrying the token goes to it. */
    val tokenHealth = TokenHealth(prefs, clock = SystemClock::elapsedRealtime, log = { Log.i("TokenHealth", it) })
    val api: ApiClient = ApiClient(prefs, identity, tokenHealth)
    val bus = StateBus(api, prefs)
    val player = PlayerController(context, api, prefs)

    /** Books saved to the phone (audiobooks/BookStore.kt): the app's own
     *  storage, filesDir/audiobooks. */
    val books = BookStore.forApp(context)

    /** The server the app talks to now, as books and their places know it. */
    fun activeServerScope(): ServerScope = ServerScope.of(prefs.serverUrl.value, prefs.pinForServer(prefs.serverUrl.value))

    /** One place per book on the phone, synced to the book's server
     *  (audiobooks/BookPositions.kt). */
    val bookPositions = PositionSync(
        store = PositionStore(java.io.File(context.filesDir, "audiobook-positions.json")),
        remote = {
            if (prefs.serverUrl.value.isBlank() || HouseholdLock.roomModeActive) null
            else ApiPositionRemote(api, prefs, activeServerScope())
        },
        person = { prefs.listenerPersonId.value },
    )

    private fun booksReconnected(context: Context) {
        scope.launch(Dispatchers.IO) {
            // Let the new network settle before asking the server.
            kotlinx.coroutines.delay(3_000)
            runCatching { BookDownloads.kickWaiting(context, books) }
            runCatching { bookPositions.pushAll(activeServerScope()) }
        }
    }

    /** Lyrics for what plays, household tier only (player/Lyrics.kt). */
    val lyrics = LyricsRepository(api)

    /** The connected server's user manual, kept per server
     *  (ui/screens/manual/ManualRepository.kt). */
    internal val manual = ManualRepository(api, java.io.File(context.filesDir, "manual"))

    /** Timer and reminder alerts: live notifications plus the local alarm
     *  mirror (alerts/TimerAlerts.kt). Started by DomovoiApplication. */
    val alerts = TimerAlerts(context, api, bus, prefs)

    /** The container guard: the shell's banner, the Containers screen and
     *  one notification per incident (alerts/IncidentAlerts.kt). Started by
     *  DomovoiApplication. */
    val containerAlerts = IncidentAlerts(context, api, bus, prefs)

    /** A screen something outside the UI asked for ("home" from a timer
     *  alert's tap, "containers" from a container incident's); AppShell
     *  navigates there and clears it. */
    val pendingRoute = MutableStateFlow<String?>(null)

    /** Help steps sent by voice to this phone's owner: one notification
     *  each (alerts/HelpAlerts.kt). Started by DomovoiApplication. */
    val helpAlerts = HelpAlerts(context, api, bus, prefs)

    /** The home-server chat a help notification's tap asked for (thread
     *  id, title); Chat opens it and clears it. */
    val pendingChatThread = MutableStateFlow<Pair<Long, String>?>(null)

    /** A new phone chat asked for (Settings → About, "Ask Domovoi for
     *  help" with no home-server chat to start); Chat opens one and clears it. */
    val pendingPhoneChat = MutableStateFlow(false)

    /** A chat something asked Chat to open ("Ask about this document" in
     *  Files): Chat opens it and clears it. */
    val pendingChat = MutableStateFlow<com.domovoi.app.ui.screens.chat.ChatOpenRequest?>(null)

    /** Phone files on their way to the server ("Your device" → upload),
     *  by row; they run in [scope], so leaving the page doesn't stop one. */
    val uploads = PhoneUploads()

    /** App-lifetime scope for fire-and-forget work that must outlive a
     *  composable (e.g. the video position save on player dispose). */
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    /** The saved books as every screen shows them, re-read whenever
     *  [books] changes (audiobooks/SavedShelf.kt). */
    val savedShelf = SavedShelf(books, scope)

    init {
        player.bookPositions = bookPositions
        // What the phone queued from a server belongs to that server
        // (player/ServerScope.kt): the player hears which server the app
        // talks to — by its pinned identity, else its address — now, and on
        // every change, and clears the old one's media on a move to another.
        player.serverChanged(ServerScope.of(prefs.serverUrl.value, prefs.pinForServer(prefs.serverUrl.value)))
        scope.launch {
            combine(prefs.serverUrl, prefs.identityPins) { url, pins ->
                ServerScope.of(url, IdentityGate.pinKey(url)?.let { pins[it] })
            }.distinctUntilChanged().collect { player.serverChanged(it) }
        }
    }

    /** Developer options (data/DeveloperOptions.kt): the benchmark and the
     *  assistant's timings, off for a household until the app version is
     *  tapped seven times; on from the start in a debug build. Kept in
     *  [prefs]. Nothing to show, so never on, without the on-device
     *  assistant (a 32-bit install). */
    val developerOptions: DeveloperOptions by lazy {
        DeveloperOptions(
            prefs,
            debugBuild = (context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0,
            available = AssistantSupport.available,
        )
    }

    /** The on-device assistant's benchmark (Settings → About & updates → developer
     *  options; M0 of the phone assistant). Built on first use. Its only
     *  network use is the
     *  model download the user starts, on a copy of the app's client WITHOUT
     *  the household token (the models come from public hosts): the same
     *  redirect and cleartext rules, no device token. */
    private val benchLazy: Lazy<AssistantBench> = lazy {
        AssistantBench(context, modelDownloader, phoneVoice, beforeLlmLoad = { if (assistantLazy.isInitialized()) assistant.unload() })
    }
    val assistantBench: AssistantBench by benchLazy

    /** The on-device assistant (assistant/AssistantController.kt): its model
     *  download, the language model in memory and the on-phone
     *  conversations. Built on first use. It never touches the network; the
     *  download job does, through [modelDownloader]. It and the benchmark
     *  each free the other's copy of the model before loading their own. */
    private val assistantLazy: Lazy<AssistantController> = lazy {
        AssistantController.forApp(
            context,
            beforeLoad = { if (benchLazy.isInitialized()) assistantBench.releaseModel() },
            // The phone's own voice for spoken replies (Settings -> Voice),
            // opened without asking any server.
            phoneVoice = { phoneVoice.openOnPhone() },
            phone = { settings -> phoneChatContext(context, settings) },
        )
    }

    /** The room names and listener name this phone's screens last saw, per
     *  server (data/HouseholdNames.kt): the phone chat's prompt reads them,
     *  never the server. */
    val householdNames: HouseholdNames by lazy { HouseholdNames.forApp(context) }

    /**
     * What a phone chat may read (assistant/AppPhoneContext.kt, N1): the
     * player's state, whether the connection is up, the cached names, the
     * timer mirror and the saved books, all as they are on the phone now.
     * Nothing here hands it the server client.
     */
    internal fun phoneChatContext(context: Context, settings: AssistantSettings): PhoneContext = AppPhoneContext(
        context = context,
        settings = settings,
        nowPlaying = { PhoneChatFacts.nowPlaying(player) },
        sleepRemainingSec = { player.sleepRemainingSec.value },
        serverSet = { prefs.serverUrl.value.isNotBlank() },
        connected = { bus.connected.value },
        rooms = { householdNames.rooms(prefs.serverUrl.value) },
        listener = { householdNames.listenerName(prefs.serverUrl.value, prefs.listenerPersonId.value) },
        mirroredTimers = { now ->
            alerts.mirrored(now).map {
                PhoneTimer(it.label ?: it.message, it.kind == "reminder", it.trigger_at_ms, it.room_id, it.masked)
            }
        },
        savedBooks = { PhoneChatFacts.savedBooks(books, bookPositions) },
    )
    val assistant: AssistantController by assistantLazy

    /** The phone's own voice (Settings → Voices): Domovoi's Piper voice
     *  through the Kotlin phonemizer once downloaded, the server's "say it
     *  like" list from `GET /api/voice` (cached per server), Android's
     *  TextToSpeech until then. Built on first use. */
    val phoneVoice: PhoneVoice by lazy { PhoneVoice(context, api, prefs) }

    /** The model download's client, used by the download job
     *  (assistant/ModelDownloadWork.kt). */
    val modelDownloader: ModelDownloader by lazy {
        val modelHttp = api.http.newBuilder()
            .apply { interceptors().removeAll { it is DeviceAuthInterceptor } }
            .pingInterval(0, TimeUnit.SECONDS)
            .build()
        ModelDownloader(modelHttp)
    }

    /** "Report this reply"'s HTTPS destination, when the owner has set one
     *  (assistant/ReplyReport.kt REPORT_DESTINATION): no household token,
     *  no redirects. Built on first use. */
    val reportUpload: ReportUpload by lazy { ReportUpload(api.http) }
}

val LocalApp = staticCompositionLocalOf<AppContainer> {
    error("AppContainer not provided")
}

/** Bottom-center toast, the web useToast() analog. Provided by the shell. */
val LocalToast = staticCompositionLocalOf<(String) -> Unit> { {} }
