package com.domovoi.app

import android.content.Context
import android.util.Log
import androidx.compose.runtime.staticCompositionLocalOf
import com.domovoi.app.alerts.IncidentAlerts
import com.domovoi.app.assistant.AssistantBench
import com.domovoi.app.assistant.ModelDownloader
import com.domovoi.app.alerts.TimerAlerts
import com.domovoi.app.data.Prefs
import com.domovoi.app.net.ApiClient
import com.domovoi.app.net.DeviceAuthInterceptor
import com.domovoi.app.net.DeviceDownloads
import com.domovoi.app.net.Discovery
import com.domovoi.app.net.IdentityGate
import com.domovoi.app.net.NetworkWatch
import com.domovoi.app.net.PhoneUploads
import com.domovoi.app.net.StateBus
import com.domovoi.app.player.LyricsRepository
import com.domovoi.app.player.PlayerController
import com.domovoi.app.player.ServerScope
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
    val api: ApiClient = ApiClient(prefs, identity)
    val bus = StateBus(api, prefs)
    val player = PlayerController(context, api, prefs)

    /** Lyrics for what plays, household tier only (player/Lyrics.kt). */
    val lyrics = LyricsRepository(api)

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

    /** Phone files on their way to the server ("Your device" → upload),
     *  by row; they run in [scope], so leaving the page doesn't stop one. */
    val uploads = PhoneUploads()

    /** App-lifetime scope for fire-and-forget work that must outlive a
     *  composable (e.g. the video position save on player dispose). */
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    init {
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

    /** The on-device assistant's benchmark (Settings → About; M0 of the
     *  phone assistant). Built on first use. Its only network use is the
     *  model download the user starts, on a copy of the app's client WITHOUT
     *  the household token (the models come from public hosts): the same
     *  redirect and cleartext rules, no device token. */
    val assistantBench: AssistantBench by lazy { AssistantBench(context, modelDownloader) }

    /** The model download's client, used by the download job
     *  (assistant/ModelDownloadWork.kt). */
    val modelDownloader: ModelDownloader by lazy {
        val modelHttp = api.http.newBuilder()
            .apply { interceptors().removeAll { it is DeviceAuthInterceptor } }
            .pingInterval(0, TimeUnit.SECONDS)
            .build()
        ModelDownloader(modelHttp)
    }
}

val LocalApp = staticCompositionLocalOf<AppContainer> {
    error("AppContainer not provided")
}

/** Bottom-center toast, the web useToast() analog. Provided by the shell. */
val LocalToast = staticCompositionLocalOf<(String) -> Unit> { {} }
